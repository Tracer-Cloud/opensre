"""Durable run identity, scheduling, deadline, and retained evidence."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from typing import Any

import psutil
import pytest
from filelock import FileLock

from config.constants.ci_repair import (
    CI_REPAIR_CRON,
    CI_REPAIR_FINISH_RESERVE_SECONDS,
    CI_REPAIR_MAX_ATTEMPTS,
)
from config.constants.turn_concurrency import OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV
from infrastructure.process.turn_capacity import (
    HeavyWorkGate,
    process_heavy_work_gate,
    reset_process_heavy_work_gate_for_tests,
)
from infrastructure.process.turn_capacity import slots as slots_module
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
)
from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_repair_loop import schedule, supervisor
from integrations.github.tools.ci_repair_loop.models import RepairRefused, RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.report import render_report
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def _run(run_id: str = "a" * 12, **kwargs: Any) -> RepairRun:
    now = time.time()
    return RepairRun(
        id=run_id,
        owner="alice",
        actor="alice",
        actor_id=123,
        repo="service",
        started_at=now,
        deadline=now + 600,
        **kwargs,
    )


def _task(run: RepairRun) -> ScheduledTask:
    return ScheduledTask(
        id=run.id,
        kind=TaskKind.MANUAL_LOOP,
        cron=CI_REPAIR_CRON,
        provider=Provider.INTERACTIVE_SHELL,
    )


def test_concurrent_reservations_and_restarts_keep_one_run_and_deadline(tmp_path: Path) -> None:
    store = RepairStore(tmp_path)
    candidates = [_run(f"{i:012x}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.reserve, candidates))
    winner = results[0][0]
    assert {run.id for run, _ in results} == {winner.id}
    assert sum(not reused for _, reused in results) == 1
    resumed, reused = RepairStore(tmp_path).reserve(_run("f" * 12))
    assert reused and resumed.deadline == winner.deadline
    with pytest.raises(ValueError, match="Another GitHub account"):
        store.reserve(_run("e" * 12).model_copy(update={"actor_id": 456}))
    winner.status = RepairStatus.FAILED
    store.save(winner)
    fresh, reused = store.reserve(_run("f" * 12))
    assert not reused and fresh.id != winner.id


def test_corrupt_or_future_storage_is_preserved(tmp_path: Path) -> None:
    store = RepairStore(tmp_path)
    original = '{"version":2,"runs":{}}'
    store.path.write_text(original)
    with pytest.raises(ValueError, match="version"):
        store.reserve(_run())
    assert store.path.read_text() == original


def test_expired_legacy_run_releases_scope_only_after_its_supervisor_stops(tmp_path: Path) -> None:
    store = RepairStore(tmp_path)
    legacy = _run().model_dump()
    legacy.pop("actor_id")
    legacy["deadline"] = time.time() - 1
    store.path.write_text(json.dumps({"version": 1, "runs": {legacy["id"]: legacy}}))
    candidate = _run("b" * 12).model_copy(update={"actor_id": 456})
    with FileLock(str(store.directory(str(legacy["id"]))) + ".execution.lock"):
        with pytest.raises(ValueError, match="stopping"):
            store.reserve(candidate)
        assert store.get(str(legacy["id"])).status is RepairStatus.QUEUED
    fresh, reused = RepairStore(tmp_path).reserve(candidate)
    assert not reused and fresh.id == candidate.id and fresh.actor_id == 456
    previous = store.get(str(legacy["id"]))
    assert previous.status is RepairStatus.TIMED_OUT and previous.actor_id == 0
    assert previous.deadline == legacy["deadline"] and previous.finished_at is not None
    resumed, reused = RepairStore(tmp_path).reserve(
        _run("c" * 12).model_copy(update={"actor_id": 456})
    )
    assert reused and resumed.id == fresh.id and resumed.deadline == fresh.deadline


class _RepairApi:
    """REST stand-in: the signed-in user, and every pull request open on a branch of its repo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, path))
        if path == "user":
            return {"login": "alice", "id": 123}
        repository, _, number = path.removeprefix("repos/").rpartition("/pulls/")
        assert number.isdigit(), path
        return {"state": "open", "head": {"sha": "head-sha", "repo": {"full_name": repository}}}


def test_expired_restart_stops_without_launching_another_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    run = _run().model_copy(update={"deadline": time.time() - 1})
    store.save(run)
    task = _task(run)
    monkeypatch.setattr(supervisor, "get_task", lambda _id: task)

    def spawn(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Expired run launched a process")

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    report = supervisor._supervise(store, run)
    assert store.get(run.id).status is RepairStatus.TIMED_OUT
    assert report.stop_schedule and "ten-minute deadline" in report
    assert (store.directory(run.id) / "result.md").read_text() == report


@pytest.mark.parametrize("cancel", [False, True])
def test_supervisor_stops_active_worker_and_its_separate_session_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    store = RepairStore(tmp_path)
    run = _run().model_copy(
        update={"deadline": time.time() + CI_REPAIR_FINISH_RESERVE_SECONDS + 1.5}
    )
    store.save(run)
    task = _task(run)
    child_file = tmp_path / "child.pid"
    real_popen = subprocess.Popen
    child = "import time; time.sleep(60)"
    program = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); "
        f"Path({str(child_file)!r}).write_text(str(p.pid)); time.sleep(60)"
    )

    def spawn(_command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        return real_popen([sys.executable, "-c", program], **kwargs)

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    monkeypatch.setattr(supervisor, "get_task", lambda _id: task)
    monkeypatch.setattr(supervisor, "CI_REPAIR_POLL_SECONDS", 0.01)
    monkeypatch.setattr(supervisor, "_cancelled", lambda _run: cancel and child_file.exists())
    report = supervisor._supervise(store, run)
    assert child_file.exists()
    pid = int(child_file.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    expected = RepairStatus.CANCELLED if cancel else RepairStatus.TIMED_OUT
    assert store.get(run.id).status is expected
    assert report.stop_schedule and "Artifacts" in report and "/loops show" in report


@pytest.fixture
def one_heavy_slot(monkeypatch: pytest.MonkeyPatch) -> Iterator[HeavyWorkGate]:
    """A process heavy-work gate of one slot that a wait notices being stopped at once."""
    monkeypatch.setenv(OPENSRE_MAX_CONCURRENT_HEAVY_WORK_ENV, "1")
    monkeypatch.setattr(slots_module, "_STOP_POLL_SECONDS", 0.01)
    reset_process_heavy_work_gate_for_tests()
    yield process_heavy_work_gate()
    reset_process_heavy_work_gate_for_tests()


def _slot_free(gate: HeavyWorkGate) -> bool:
    if gate.try_acquire():
        gate.release()
        return True
    return False


def _no_worker(*_args: Any, **_kwargs: Any) -> None:
    pytest.fail("launched a worker without a heavy-work slot")


def test_supervisor_holds_a_heavy_work_slot_for_the_workers_whole_life(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_heavy_slot: HeavyWorkGate
) -> None:
    """The worker clones and runs Codex in its own process, which no gate there can bound."""
    # Arrange: a worker that is still running at its first poll and exits at its second.
    store = RepairStore(tmp_path)
    run = _run()
    store.save(run)
    task = _task(run)
    held: list[bool] = []

    class _Worker:
        pid = 0

        def __init__(self) -> None:
            self.polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            held.append(not _slot_free(one_heavy_slot))
            return None if self.polls == 1 else 0

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 0

    def spawn(*_args: Any, **_kwargs: Any) -> _Worker:
        held.append(not _slot_free(one_heavy_slot))
        return _Worker()

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    monkeypatch.setattr(supervisor, "get_task", lambda _id: task)
    monkeypatch.setattr(supervisor, "CI_REPAIR_POLL_SECONDS", 0)

    # Act
    supervisor._supervise(store, run)

    # Assert: held from launch through every poll, and free once the worker is reaped.
    assert len(held) >= 3 and all(held)
    assert _slot_free(one_heavy_slot)


def test_a_repair_that_never_gets_a_heavy_work_slot_times_out_without_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_heavy_slot: HeavyWorkGate
) -> None:
    # Arrange: the only slot is taken and the run's cutoff is moments away.
    store = RepairStore(tmp_path)
    run = _run().model_copy(
        update={"deadline": time.time() + CI_REPAIR_FINISH_RESERVE_SECONDS + 0.2}
    )
    store.save(run)
    task = _task(run)
    monkeypatch.setattr(supervisor.subprocess, "Popen", _no_worker)
    monkeypatch.setattr(supervisor, "get_task", lambda _id: task)
    assert one_heavy_slot.try_acquire()

    # Act
    report = supervisor._supervise(store, run)

    # Assert: a terminal, explained outcome instead of an OOM risk.
    finished = store.get(run.id)
    assert finished.status is RepairStatus.TIMED_OUT
    assert "Too many heavy operations" in finished.reason
    assert isinstance(report, TaskReport) and report.stop_schedule
    one_heavy_slot.release()


def test_stopping_a_repair_that_waits_for_a_heavy_work_slot_cancels_it_without_a_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, one_heavy_slot: HeavyWorkGate
) -> None:
    # Arrange: the only slot is taken, and the loop is stopped once supervision has begun.
    store = RepairStore(tmp_path)
    run = _run().model_copy(
        update={"deadline": time.time() + CI_REPAIR_FINISH_RESERVE_SECONDS + 30}
    )
    store.save(run)
    checks = iter([False])
    monkeypatch.setattr(supervisor.subprocess, "Popen", _no_worker)
    monkeypatch.setattr(supervisor, "_cancelled", lambda _run: next(checks, True))
    assert one_heavy_slot.try_acquire()

    # Act: without the stop the wait would last the thirty seconds to the cutoff.
    supervisor._supervise(store, run)

    # Assert
    assert store.get(run.id).status is RepairStatus.CANCELLED
    one_heavy_slot.release()


def test_schedule_reuses_active_run_instead_of_resetting_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    monkeypatch.setattr(schedule, "get_task", tasks.get)

    def add(task: ScheduledTask) -> ScheduledTask:
        tasks[task.id] = task
        return task

    monkeypatch.setattr(schedule, "add_task", add)
    first, reused, _ = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store
    )
    second, reused_again, _ = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store
    )
    assert not reused and reused_again
    assert first.id == second.id and first.deadline == second.deadline
    assert first.owner == "alice"
    assert len(tasks) == 1 and next(iter(tasks.values())).cron == CI_REPAIR_CRON


def test_schedule_refuses_when_owner_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token login is not a substitute for the owner the model must supply."""
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    store = RepairStore(tmp_path)

    with pytest.raises(RepairRefused, match="explicit GitHub owner"):
        schedule.schedule_repair(owner="", repo="service", pr_number=1, store=store)
    assert store.newest_for(123) is None


def test_failure_retains_diagnostics_and_report_contains_evidence_links(tmp_path: Path) -> None:
    run = _run(pr_number=7).model_copy(
        update={
            "status": RepairStatus.FAILED,
            "attempts": 4,
            "failed_run_url": "https://github.com/alice/demo/actions/runs/10",
            "reason": "CI remains failing.",
        }
    )
    report = render_report(run, tmp_path)
    assert all(line.startswith("- ") for line in report.splitlines())
    assert run.pr_url in report and run.failed_run_url in report
    assert "**Repair attempts:** 4" in report and "Temporary artifacts retained" in report


@pytest.mark.parametrize("changed_head", [False, True])
def test_worker_retries_then_credits_only_the_verified_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_head: bool,
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run(pr_number=1, fast_checks=True)
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "select_coding_agent", lambda: ("codex", "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: _RepairApi())

    def clone(_url: str, workspace: str, **_kwargs: Any) -> None:
        Path(workspace).mkdir()

    monkeypatch.setattr(worker, "clone_repository", clone)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    calls = 0

    def repair(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        assert kwargs["allowed_paths"] == frozenset({"calculator.py"})
        assert kwargs["expected_source_head_sha"] == "broken"
        assert kwargs["registration_seconds"] == 0
        assert kwargs["settle_seconds"] == 0
        assert kwargs["poll_interval_seconds"] == 2
        if calls < CI_REPAIR_MAX_ATTEMPTS:
            return {"success": False, "error_kind": "checks_failed"}
        return {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        finished = calls == CI_REPAIR_MAX_ATTEMPTS
        return {
            "state": "OPEN",
            "headRefOid": (
                "someone-else" if finished and changed_head else "fixed" if finished else "broken"
            ),
            "statusCheckRollup": [
                {
                    "conclusion": "SUCCESS" if finished else "FAILURE",
                    "detailsUrl": run.repository_url + "/actions/runs/123",
                }
            ],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "_read_pr", pr)
    worker.execute_repair(run, store)
    assert run.attempts == CI_REPAIR_MAX_ATTEMPTS
    if changed_head:
        assert run.status is RepairStatus.FAILED and not run.checks_passed
    else:
        assert run.status is RepairStatus.SUCCEEDED and run.checks_passed
        assert run.fixed_sha == "fixed" and run.passed_run_url
    # A finished run keeps its records, not its checkout, whatever the outcome.
    supervisor.finish_run(store, run)
    assert not Path(run.workspace).exists()


def test_attempt_record_adds_the_backend_and_phase_times_to_the_repair_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where the minutes went, per attempt: setup lands in the first record, nothing is lost."""
    from itertools import count

    from integrations.github.tools.ci_fix.timing import PhaseTimer
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: every clock read is one second after the previous one.
    store = RepairStore(tmp_path)
    run = _run(pr_number=1, fast_checks=True)
    ticks = count()
    timer = PhaseTimer(clock=lambda: float(next(ticks)))
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "select_coding_agent", lambda: ("codex", "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(worker, "clone_repository", lambda _url, ws, **_kw: Path(ws).mkdir())
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    output = {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    def repair(**kwargs: Any) -> dict[str, Any]:
        with kwargs["timer"].phase("coding_agent"):
            return output

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        head = "fixed" if run.attempts else "broken"
        conclusion = "SUCCESS" if run.attempts else "FAILURE"
        return {
            "state": "OPEN",
            "headRefOid": head,
            "statusCheckRollup": [{"conclusion": conclusion}],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "_read_pr", pr)

    # Act
    worker.execute_repair(run, store, timer)

    # Assert: the repair output is unchanged; backend and phase times are added keys.
    record = json.loads((store.directory(run.id) / "attempt-1.json").read_text())
    expected = {
        "agent_probe": 1.0,
        "github_user": 1.0,
        "clone": 1.0,
        "wait_for_failure": 1.0,
        "coding_agent": 1.0,
    }
    assert record == {**output, "coding_agent": "codex", "phase_seconds": expected}
    assert run.status is RepairStatus.SUCCEEDED
    assert run.coding_agent == "codex"
    assert run.phase_seconds == expected


def test_a_repair_probes_the_coding_agents_once_for_all_its_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pi --version`` alone took 2-3 s here; three attempts used to sweep seven times."""
    from unittest.mock import MagicMock

    from integrations.coding_agent import CodingResult, run_coding_task, verify_coding_agent
    from integrations.coding_agent.runner import _BACKENDS
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: auto finds Pi signed out, then Codex ready.
    pi_probe = MagicMock(return_value=(False, "pi has no credentials"))
    codex_probe = MagicMock(return_value=(True, "codex ready"))
    codex_run = MagicMock(return_value=CodingResult(success=True, summary="fixed"))
    monkeypatch.delenv("CODING_AGENT", raising=False)
    monkeypatch.setattr(
        "integrations.coding_agent.runner.hosted_openai_subprocess_env", lambda: None
    )
    monkeypatch.setitem(_BACKENDS, "pi", (MagicMock(), pi_probe))
    monkeypatch.setitem(_BACKENDS, "claude-code", (MagicMock(), pi_probe))
    monkeypatch.setitem(_BACKENDS, "codex", (codex_run, codex_probe))
    store = RepairStore(tmp_path)
    run = _run(pr_number=1, fast_checks=True)
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(worker, "clone_repository", lambda _url, ws, **_kw: Path(ws).mkdir())
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    def repair(**_kwargs: Any) -> dict[str, Any]:
        # What each coding step of run_ci_fix does: check readiness, then run the agent.
        assert verify_coding_agent() == (True, "codex: codex ready")
        run_coding_task("fix", workspace="/w", model=None, timeout_sec=60)
        return {"success": False, "error_kind": "checks_failed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        return {
            "state": "OPEN",
            "headRefOid": "broken",
            "statusCheckRollup": [{"conclusion": "FAILURE"}],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "_read_pr", pr)

    # Act
    worker.execute_repair(run, store)

    # Assert: one sweep at worker start served every attempt's check and run.
    assert run.attempts == CI_REPAIR_MAX_ATTEMPTS
    assert codex_run.call_count == CI_REPAIR_MAX_ATTEMPTS
    assert pi_probe.call_count == 2  # Pi and Claude Code, once each
    assert codex_probe.call_count == 1


def test_repair_stops_after_three_failed_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run(pr_number=7)
    store.directory(run.id).mkdir()
    calls = 0

    def repair(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"success": False, "error_kind": "checks_failed"}

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        worker,
        "_read_pr",
        lambda *_args: {
            "state": "OPEN",
            "headRefOid": "broken",
            "statusCheckRollup": [
                {
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://github.com/alice/demo/actions/runs/1",
                }
            ],
        },
    )
    worker._repair(run, store, "test-token")
    assert calls == CI_REPAIR_MAX_ATTEMPTS
    assert run.attempts == CI_REPAIR_MAX_ATTEMPTS
    assert run.status is RepairStatus.FAILED
    assert run.reason == f"Stopped after {CI_REPAIR_MAX_ATTEMPTS} failed repair attempts."


def test_account_change_stops_before_any_remote_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    api = _RepairApi()
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "select_coding_agent", lambda: ("codex", "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: api)
    run = _run().model_copy(update={"actor_id": 456})

    def refuse_clone(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("An unauthorized worker must not reach a repository checkout.")

    monkeypatch.setattr(worker, "clone_repository", refuse_clone)
    with pytest.raises(ValueError, match="account changed"):
        worker.execute_repair(run, RepairStore(tmp_path))
    assert api.calls == [("GET", "user")]


def test_real_cron_tick_saves_terminal_report_before_stopping_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    from datetime import UTC, datetime, timedelta

    from apscheduler.schedulers.background import BackgroundScheduler

    from infrastructure.scheduling.scheduler import delivery_bundle, runner
    from infrastructure.scheduling.scheduler.storage import get_runs, get_task
    from integrations.manual_loop_runner import run_manual_prompt_loop
    from tests.scheduler._bundle import runners_with_agent

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: tmp_path / "scheduler.db",
    )
    store = RepairStore(tmp_path / "repair")
    monkeypatch.setattr(supervisor, "RepairStore", lambda: store)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    run, _, _ = schedule.schedule_repair(owner="alice", repo="service", pr_number=1, store=store)
    run.status, run.reason = RepairStatus.TIMED_OUT, "Fixture runner reached its deadline."
    store.save(run)
    delivered = threading.Event()
    evidence: list[str] = []

    class Delivery:
        def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
            # Inspect the real stores at the delivery boundary, after persistence and stop.
            saved = get_runs(task.id)
            current = get_task(task.id)
            if saved and saved[0].report == message and current is not None and not current.enabled:
                evidence.append(message)
            delivered.set()
            return True, "", "local-report"

    adapters = delivery_bundle.ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: Delivery()})
    monkeypatch.setattr(delivery_bundle, "_installed", adapters)
    scheduler = runner._build_scheduler(BackgroundScheduler)
    task = get_task(run.id)
    assert task is not None
    try:
        scheduler.add_job(
            runner._scheduled_job,
            trigger=runner._make_trigger(task),
            id=run.id,
            args=[run.id, runners_with_agent(run_manual_prompt_loop)],
            next_run_time=datetime.now(UTC) + timedelta(milliseconds=100),
        )
        scheduler.start()
        assert delivered.wait(15), "The scheduler did not fire the registered repair task."
        assert len(evidence) == 1 and "/loops show" in evidence[0]
        assert get_task(run.id) is not None
    finally:
        scheduler.shutdown(wait=True)


def test_existing_green_pr_still_waits_for_late_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification
    from integrations.github.tools.ci_repair_loop import worker

    run = _run(pr_number=1)
    states = iter(
        [
            {
                "state": "OPEN",
                "headRefOid": "green-head",
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            },
            {"state": "CLOSED"},
        ]
    )
    monkeypatch.setattr(worker, "_read_pr", lambda *_args: next(states))
    checked = []

    def verify(ctx: Any, **kwargs: Any) -> CheckVerification:
        checked.append(kwargs["expected_head_sha"])
        assert "registration_seconds" not in kwargs
        assert "settle_seconds" not in kwargs
        assert "poll_interval_seconds" not in kwargs
        return CheckVerification(state=CheckState.FAILED, check_names=("late security",))

    monkeypatch.setattr(worker, "wait_for_pr_checks", verify, raising=False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    worker._repair(run, RepairStore(tmp_path), "test-token")
    assert checked == ["green-head"]
    assert run.status is RepairStatus.CANCELLED


def test_a_green_pr_with_skipped_jobs_is_verified_not_waited_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped jobs settle a rollup; they must not keep a green PR polling until the deadline."""
    from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: a settled rollup of successes and skips, and a verification that passes
    run = _run(pr_number=6054)
    pr = {
        "state": "OPEN",
        "headRefOid": "green-head",
        "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}],
    }
    monkeypatch.setattr(worker, "_read_pr", lambda *_args: pr)

    def verify(_ctx: Any, **_kwargs: Any) -> CheckVerification:
        return CheckVerification(state=CheckState.PASSED, check_names=())

    monkeypatch.setattr(worker, "wait_for_pr_checks", verify, raising=False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    # Act
    worker._repair(run, RepairStore(tmp_path), "test-token")

    # Assert
    assert run.status is RepairStatus.SUCCEEDED
    assert run.reason == "The selected PR is already green; no repair was made."


def test_reports_require_the_recorded_github_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import tool

    store = RepairStore(tmp_path)
    run = _run(pr_number=123)
    store.save(run)
    monkeypatch.setattr(tool, "RepairStore", lambda: store)
    monkeypatch.setattr(tool, "configured_token", lambda _token: "request-token", raising=False)

    class Reader:
        actor = "alice"
        account_id = 456

        def request(self, *_args: Any) -> dict[str, Any]:
            return {"login": self.actor, "id": self.account_id}

    reader = Reader()
    monkeypatch.setattr(tool, "GitHubRestClient", lambda _token: reader, raising=False)
    rejected = tool.get_ci_repair_loop(run.id)
    assert not rejected["ok"] and run.repo not in str(rejected) and run.pr_url not in str(rejected)
    reader.actor = "renamed-alice"
    reader.account_id = 123
    allowed = tool.get_ci_repair_loop(run.id)
    assert allowed["ok"] and allowed["pr_url"] == run.pr_url
    store.save(run.model_copy(update={"actor_id": 0}))
    assert not tool.get_ci_repair_loop(run.id)["ok"]


def test_the_report_without_an_id_is_this_accounts_newest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "What did the last repair do?" needs no run id."""
    from integrations.github.tools.ci_repair_loop import tool

    # Arrange: two runs of this account (the newer started later) and one of another account
    store = RepairStore(tmp_path)
    older = _run(run_id="b" * 12, pr_number=1)
    newer = _run(run_id="c" * 12, pr_number=2).model_copy(
        update={"started_at": older.started_at + 60}
    )
    foreign = _run(run_id="d" * 12, pr_number=3).model_copy(
        update={"actor_id": 999, "started_at": older.started_at + 120}
    )
    for run in (older, newer, foreign):
        store.save(run)
    monkeypatch.setattr(tool, "RepairStore", lambda: store)
    monkeypatch.setattr(tool, "configured_token", lambda _token: "request-token", raising=False)

    class Reader:
        def request(self, *_args: Any) -> dict[str, Any]:
            return {"login": "alice", "id": 123}

    monkeypatch.setattr(tool, "GitHubRestClient", lambda _token: Reader(), raising=False)

    # Act
    report = tool.get_ci_repair_loop()

    # Assert: the newest of this account's runs, never another account's
    assert report["ok"] and report["pr_url"] == newer.pr_url


def test_the_report_without_an_id_says_when_there_are_no_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import tool

    # Arrange: an empty store
    monkeypatch.setattr(tool, "RepairStore", lambda: RepairStore(tmp_path))
    monkeypatch.setattr(tool, "configured_token", lambda _token: "request-token", raising=False)

    class Reader:
        def request(self, *_args: Any) -> dict[str, Any]:
            return {"login": "alice", "id": 123}

    monkeypatch.setattr(tool, "GitHubRestClient", lambda _token: Reader(), raising=False)

    # Act
    report = tool.get_ci_repair_loop()

    # Assert
    assert report["ok"] is False and "no CI repair runs yet" in report["error"]


def test_a_pushed_repair_counts_as_success_when_the_next_attempt_finds_nothing_to_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pushed repair that leaves nothing to fix is a verified success, not a failed attempt."""
    from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: the PR still reads as failing at the tick, the fix runner then finds nothing to fix,
    # and the pushed head verifies green
    run = _run(pr_number=6404).model_copy(
        update={"initial_sha": "old-head", "attempts": 1, "pushed_shas": ["new-head"]}
    )
    recorded: list[dict[str, Any]] = []
    store = RepairStore(tmp_path)
    store.directory(run.id).mkdir(parents=True, exist_ok=True)

    def verified_green(ctx: Any, **kwargs: Any) -> CheckVerification:  # noqa: ARG001
        return CheckVerification(state=CheckState.PASSED, check_names=("quality",))

    reads = iter(
        [
            {
                "state": "OPEN",
                "headRefOid": "new-head",
                "statusCheckRollup": [{"conclusion": "FAILURE"}],
            },
            {
                "state": "OPEN",
                "headRefOid": "new-head",
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            },
            {
                "state": "OPEN",
                "headRefOid": "new-head",
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            },
        ]
    )
    monkeypatch.setattr(worker, "_read_pr", lambda *_args: next(reads))
    monkeypatch.setattr(
        worker, "run_ci_fix", lambda **_kw: {"success": True, "error_kind": "no_failing_checks"}
    )
    monkeypatch.setattr(worker, "record_ci_fix_outcome", recorded.append)
    monkeypatch.setattr(worker, "wait_for_pr_checks", verified_green, raising=False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    # Act
    worker._repair(run, store, "test-token")

    # Assert: the run is a verified success on the pushed head, not a failed attempt
    assert run.status is RepairStatus.SUCCEEDED
    assert run.checks_passed is True and run.fixed_sha == "new-head"
    assert run.reason == "The repair commit passed CI."
    assert "no_failing_checks" not in run.attempt_errors
    # The ledger sees the verified pass, not only the attempt with no check state
    assert recorded[-1]["success"] is True and recorded[-1]["checks_state"] == "passed"
    assert recorded[-1]["fix_head_sha"] == "new-head"
    # Same ledger identity as the normal success path: the head the repair started from
    assert recorded[-1]["source_head_sha"] == "old-head"


def test_a_green_head_pushed_by_someone_else_is_not_credited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a head this run pushed may become the repair commit."""
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: the run pushed "mine", but the PR head is now a contributor's commit that the
    # fixer finds nothing left to repair on, and that then turns green
    run = _run(pr_number=6404).model_copy(
        update={"initial_sha": "old-head", "attempts": 1, "pushed_shas": ["mine"]}
    )
    store = RepairStore(tmp_path)
    store.directory(run.id).mkdir(parents=True, exist_ok=True)
    reads = iter(
        [
            {
                "state": "OPEN",
                "headRefOid": "theirs",
                "statusCheckRollup": [{"conclusion": "FAILURE"}],
            },
            {
                "state": "OPEN",
                "headRefOid": "theirs",
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            },
        ]
    )

    def verify(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("A head this run did not push was verified for credit")

    monkeypatch.setattr(worker, "_read_pr", lambda *_args: next(reads))
    monkeypatch.setattr(
        worker, "run_ci_fix", lambda **_kw: {"success": True, "error_kind": "no_failing_checks"}
    )
    monkeypatch.setattr(worker, "wait_for_pr_checks", verify)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    # Act
    worker._repair(run, store, "test-token")

    # Assert: the contributor's green head never becomes this run's repair commit
    assert run.fixed_sha == "" and run.checks_passed is False
    assert run.status is RepairStatus.FAILED
    assert run.attempt_errors == ["no_failing_checks"]


@pytest.mark.parametrize(
    ("fix_head_sha", "expected_calls", "expected_status"),
    [("", 2, RepairStatus.QUEUED), ("pushed", 1, RepairStatus.FAILED)],
    ids=["follows-new-head", "replaced-push-stops"],
)
def test_a_head_that_moves_before_the_push_is_read_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fix_head_sha: str,
    expected_calls: int,
    expected_status: RepairStatus,
) -> None:
    """Only a superseded attempt that pushed nothing may repair the head that replaced it."""
    from integrations.github.tools.ci_fix.storage import database
    from integrations.github.tools.ci_repair_loop import worker

    monkeypatch.setattr(database, "database_path", lambda: tmp_path / "repairs.db")
    run = _run()
    store = RepairStore(tmp_path / "runs")
    store.directory(run.id).mkdir(parents=True)
    heads = iter(["source-head", "moved-head", "fixed"])
    calls: list[str] = []

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        return {
            "state": "OPEN",
            "headRefOid": next(heads),
            "statusCheckRollup": [{"conclusion": "FAILURE"}],
        }

    def repair(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["expected_source_head_sha"])
        if len(calls) == 1:
            return {
                "success": False,
                "error_kind": "checks_superseded",
                "fix_head_sha": fix_head_sha,
            }
        return {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    monkeypatch.setattr(worker, "_read_pr", pr)
    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)

    worker._repair(run, store, "test-token")

    assert calls == ["source-head", "moved-head"][:expected_calls]
    assert run.status is expected_status
    assert run.checks_passed is (expected_calls == 2)


class _PullRequestApi:
    """REST stand-in: the signed-in user plus one pull request with a chosen head repository."""

    def __init__(self, *, state: str, head_full_name: str) -> None:
        self._pull = {"state": state, "head": {"repo": {"full_name": head_full_name}}}

    def request(self, _method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        if path == "user":
            return {"login": "alice", "id": 123}
        assert path == "repos/Tracer-Cloud/opensre/pulls/6408", path
        return self._pull


@pytest.mark.parametrize(
    ("state", "head", "expected"),
    [
        ("open", "someone/opensre", "comes from someone/opensre"),
        ("closed", "Tracer-Cloud/opensre", "PR #6408 is closed"),
    ],
)
def test_scheduling_refuses_a_fork_or_closed_pull_request_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, head: str, expected: str
) -> None:
    """A target the loop could never push to is refused before anything is scheduled."""
    from integrations.github.tools.ci_repair_loop import schedule

    # Arrange
    api = _PullRequestApi(state=state, head_full_name=head)
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: api)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "t", raising=False)
    store = RepairStore(tmp_path)

    # Act / Assert: refused with the reason and what to choose instead; nothing stays reserved
    with pytest.raises(ValueError, match=expected):
        schedule.schedule_repair(owner="Tracer-Cloud", repo="opensre", pr_number=6408, store=store)
    assert store.newest_for(123) is None


def test_a_same_repository_open_pull_request_passes_the_scheduling_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import schedule

    # Arrange
    api = _PullRequestApi(state="open", head_full_name="Tracer-Cloud/opensre")

    # Act / Assert: no refusal from the check itself
    schedule._require_repairable(api, "Tracer-Cloud", "opensre", 6408)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            "unsupported_pr_branch",
            "PR #6408 comes from a fork; the loop only pushes to branches inside Tracer-Cloud/opensre",
        ),
        ("push_failed", "the push was refused"),
        ("no_changes", "made no change"),
        ("something_new", "something_new."),
    ],
)
def test_attempt_reasons_read_as_plain_sentences(error: str, expected: str) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    # Arrange
    run = _run(pr_number=6408).model_copy(update={"owner": "Tracer-Cloud", "repo": "opensre"})

    # Act / Assert
    assert expected in worker._reason_for(error, run)


def test_a_finished_run_drops_its_checkout_but_keeps_its_records(tmp_path: Path) -> None:
    """Each checkout is hundreds of megabytes on the shared volume; nothing reads it afterwards."""
    from integrations.github.tools.ci_repair_loop.storage import CHECKOUT_REMOVED

    # Arrange: a finished run whose directory holds a checkout and its records
    store = RepairStore(tmp_path)
    directory = store.directory("abcdefabcdef")
    checkout = directory / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (directory / "attempt-1.json").write_text("{}", encoding="utf-8")
    run = _run(pr_number=6408).model_copy(
        update={
            "id": "abcdefabcdef",
            "workspace": str(checkout),
            "status": RepairStatus.FAILED,
        }
    )

    # Act: the shared finish path every terminal outcome goes through
    supervisor.finish_run(store, run)

    # Assert
    assert not checkout.exists()
    assert (directory / "attempt-1.json").exists() and (directory / "result.md").exists()
    assert run.cleanup == CHECKOUT_REMOVED


def test_a_checkout_that_survives_removal_is_reported_as_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import storage
    from integrations.github.tools.ci_repair_loop.storage import CHECKOUT_RETAINED

    # Arrange: removal silently does nothing, as on a permission error
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(storage.shutil, "rmtree", lambda *_a, **_kw: None)
    run = _run(pr_number=6408).model_copy(update={"workspace": str(checkout)})

    # Act
    RepairStore(tmp_path).discard_checkout(run)

    # Assert: the report never claims space that was not reclaimed
    assert checkout.exists()
    assert run.cleanup == CHECKOUT_RETAINED


def test_the_scheduling_tool_returns_the_refusal_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user reads why the pull request was refused and what to choose instead."""
    from integrations.github.tools.ci_repair_loop import tool as repair_tool
    from integrations.github.tools.ci_repair_loop.models import RepairRefused

    # Arrange
    refusal = "PR #6408 comes from someone/opensre; choose a pull request from this repository."

    def refuse(**_kwargs: Any) -> tuple[RepairRun, bool, str | None]:
        raise RepairRefused(refusal)

    monkeypatch.setattr(repair_tool, "schedule_repair", refuse)
    monkeypatch.setattr(repair_tool, "RepairStore", lambda: None)

    # Act
    result = repair_tool.schedule_ci_repair_loop(
        owner="Tracer-Cloud", repo="opensre", pr_number=6408, github_token="t"
    )

    # Assert: the reason is in the error too, which is all the model and telemetry read
    assert result["ok"] is False
    assert result["response_text"] == refusal
    assert result["error_kind"] == "refused"
    assert refusal in result["error"]


def _signed_in_api(_token: str) -> _PullRequestApi:
    return _PullRequestApi(state="open", head_full_name="Tracer-Cloud/opensre")


def test_a_failed_repair_read_names_its_cause_not_a_generic_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production: every failed read said only 'check your GitHub connection and run id'."""
    from integrations.github.tools.ci_repair_loop import tool as repair_tool

    # Arrange: GitHub refuses the token while the account is looked up
    class _RefusingClient:
        def __init__(self, _token: str) -> None:
            pass

        def request(self, _method: str, path: str) -> Any:
            raise GitHubApiError("Bad credentials", status_code=HTTPStatus.UNAUTHORIZED, path=path)

    monkeypatch.setattr(repair_tool, "RepairStore", lambda: RepairStore(tmp_path))
    monkeypatch.setattr(repair_tool, "configured_token", lambda _token: "t")
    monkeypatch.setattr(repair_tool, "GitHubRestClient", _RefusingClient)

    # Act
    unauthorized = repair_tool.get_ci_repair_loop(task_id="a" * 12, github_token="t")
    monkeypatch.setattr(repair_tool, "GitHubRestClient", _signed_in_api)
    unknown = repair_tool.get_ci_repair_loop(task_id="not-a-run", github_token="t")

    # Assert
    assert unauthorized["ok"] is False
    assert f"{HTTPStatus.UNAUTHORIZED.value}" in unauthorized["error"]
    assert "Bad credentials" in unauthorized["error"]
    assert "Invalid CI repair run id" in unknown["error"]


def test_an_active_run_is_reused_without_the_pull_request_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR that closes mid-run still returns the active run with its original deadline."""
    from integrations.github.tools.ci_repair_loop import schedule

    # Arrange: an active run for the target, and a GitHub that now reports the PR closed
    store = RepairStore(tmp_path)
    active = _run(pr_number=6408).model_copy(update={"owner": "Tracer-Cloud", "repo": "opensre"})
    store.reserve(active)
    api = _PullRequestApi(state="closed", head_full_name="Tracer-Cloud/opensre")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: api)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "t", raising=False)
    monkeypatch.setattr(schedule, "get_task", lambda _id: _EnabledTask())

    # Act
    run, reused, _next_run = schedule.schedule_repair(
        owner="Tracer-Cloud", repo="opensre", pr_number=6408, store=store
    )

    # Assert
    assert reused and run.id == active.id


class _EnabledTask:
    enabled = True
    next_run = "soon"


class _PullRequestLookupDown:
    """REST stand-in: the signed-in user answers, the pull request lookup fails."""

    def request(self, _method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        if path == "user":
            return {"login": "alice", "id": 123}
        raise GitHubApiError("GitHub API request failed: timed out", path=path)


def test_a_failed_pull_request_lookup_still_returns_the_active_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse never waits on GitHub; only a fresh reservation needs the lookup."""
    from integrations.github.tools.ci_repair_loop import schedule

    # Arrange
    store = RepairStore(tmp_path)
    active = _run(pr_number=6408).model_copy(update={"owner": "Tracer-Cloud", "repo": "opensre"})
    store.reserve(active)
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _PullRequestLookupDown())
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "t", raising=False)
    monkeypatch.setattr(schedule, "get_task", lambda _id: _EnabledTask())

    # Act
    run, reused, _next_run = schedule.schedule_repair(
        owner="Tracer-Cloud", repo="opensre", pr_number=6408, store=store
    )

    # Assert
    assert reused and run.id == active.id


def test_interrupted_registration_recovers_original_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    original, _ = store.reserve(_run(pr_number=1).model_copy(update={"actor": "old-alice"}))
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)

    def add(task: ScheduledTask) -> ScheduledTask:
        tasks[task.id] = task
        return task

    monkeypatch.setattr(schedule, "add_task", add)
    resumed, reused, _ = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store
    )
    assert reused and resumed.id == original.id and resumed.deadline == original.deadline
    assert resumed.status is RepairStatus.QUEUED and len(tasks) == 1


def test_evidence_links_require_the_reported_outcome() -> None:
    from integrations.github.tools.ci_repair_loop import worker

    prefix = "https://github.com/alice/demo/actions/runs/"
    rows = [
        {"conclusion": "", "status": "QUEUED", "detailsUrl": prefix + "queued"},
        {"conclusion": "ACTION_REQUIRED", "detailsUrl": prefix + "action-required"},
        {"conclusion": "SKIPPED", "detailsUrl": prefix + "skipped"},
        {"conclusion": "SUCCESS", "detailsUrl": prefix + "passed"},
    ]
    assert worker._run_link(rows, failed=True) == prefix + "action-required"
    assert worker._run_link(rows, failed=False) == prefix + "passed"


def test_hosted_scheduler_registers_without_an_os_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the hosted gateway there is no systemctl; the in-process scheduler takes the task."""
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "add_task", lambda task: tasks.setdefault(task.id, task))

    def refuse(**_kwargs: Any) -> None:
        raise AssertionError("the OS service must not be touched on a hosted gateway")

    monkeypatch.setattr(schedule, "ensure_background_service", refuse)

    run, reused, next_run = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store, scheduler_in_process=True
    )

    assert not reused
    assert run.id in tasks and next_run
    assert store.get(run.id).registered


def test_new_repair_schedule_is_due_now(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from infrastructure.scheduling.scheduler.runner import compute_next_run

    due = datetime(2026, 10, 3, 15, 9, 7, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            _ = tz
            return due

    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "datetime", _FrozenDateTime)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kwargs: None)
    monkeypatch.setattr(schedule, "get_task", tasks.get)

    def add(task: ScheduledTask) -> ScheduledTask:
        tasks[task.id] = task
        return task

    monkeypatch.setattr(schedule, "add_task", add)

    run, reused, next_run = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store
    )

    assert not reused
    task = tasks[run.id]
    assert task.cron == CI_REPAIR_CRON
    assert next_run == due.isoformat()
    assert task.next_run == due.isoformat()
    assert compute_next_run(task, due) == "2026-10-03T15:09:30+00:00"
    assert task.next_run != compute_next_run(task, due)


class _RecordedEvents:
    """Stands in for the analytics client so each milestone's event and properties are seen."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def capture(self, event: str, properties: dict[str, object] | None = None) -> None:
        self.events.append((str(event), dict(properties or {})))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> _RecordedEvents:
    from infrastructure.analytics import capture

    events = _RecordedEvents()
    monkeypatch.setattr(capture, "get_analytics", lambda: events)
    return events


_REPOSITORY = "alice/service"


@pytest.mark.parametrize("remote", [True, False], ids=["gateway", "shell"])
def test_only_a_gateway_scheduled_loop_records_that_remote_monitoring_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: _RecordedEvents, remote: bool
) -> None:
    # Arrange
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "add_task", lambda task: tasks.setdefault(task.id, task))
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)

    # Act: the second request reuses the active run, so monitoring did not start again
    run, _, _ = schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store, scheduler_in_process=remote
    )
    schedule.schedule_repair(
        owner="alice", repo="service", pr_number=1, store=store, scheduler_in_process=remote
    )

    # Assert
    assert store.get(run.id).remote is remote
    started = (
        "remote_ci_monitoring_started",
        {"repair_run_id": run.id, "repository": _REPOSITORY, "demo": False, "pr_number": 1},
    )
    assert recorded.events == ([started] if remote else [])


@pytest.mark.parametrize("remote", [True, False], ids=["gateway", "shell"])
def test_the_seeded_demo_records_its_failing_pull_request_once_on_either_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: _RecordedEvents, remote: bool
) -> None:
    # Arrange
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "add_task", lambda task: tasks.setdefault(task.id, task))
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    seeded = "opensre-ci-repair-demo-g0xd"

    # Act: the seeded-demo tool schedules its pull request; a repeat reuses the active run
    run, _, _ = schedule.schedule_repair(
        owner="alice",
        repo=seeded,
        pr_number=1,
        store=store,
        scheduler_in_process=remote,
        fast_checks=True,
    )
    schedule.schedule_repair(
        owner="alice",
        repo=seeded,
        pr_number=1,
        store=store,
        scheduler_in_process=remote,
        fast_checks=True,
    )

    # Assert
    on_pr = {
        "repair_run_id": run.id,
        "repository": f"alice/{seeded}",
        "demo": True,
        "pr_number": 1,
    }
    started = ("remote_ci_monitoring_started", on_pr)
    failure = ("test_ci_failure_triggered", {**on_pr, "remote": remote})
    assert recorded.events == ([started, failure] if remote else [failure])


def test_only_the_pull_request_this_account_seeded_here_is_scheduled_as_the_demo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local skill schedules its seeded PR without fast_checks; provenance marks it."""
    from integrations.github.tools.ci_repair_loop import seeded

    # Arrange: what this process seeded; the scheduling account is 123, every head "head-sha"
    monkeypatch.setattr(seeded, "_SEEDED", {})
    demo = "opensre-ci-repair-demo-g0xd"
    seeded.remember_seeded_pull(123, "alice", demo, 1, "head-sha")
    seeded.remember_seeded_pull(456, "alice", demo, 2, "head-sha")
    seeded.remember_seeded_pull(123, "alice", demo, 3, "seeded-head")
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "add_task", lambda task: tasks.setdefault(task.id, task))
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    targets = [
        ("Alice", demo, 1),  # seeded by this account, head unchanged
        ("alice", demo, 2),  # seeded by another account
        ("alice", demo, 3),  # a commit replaced the seeded head
        ("alice", "opensre-ci-repair-demo-zz99", 1),  # named like the demo only
    ]

    # Act
    runs = [
        schedule.schedule_repair(owner=owner, repo=repo, pr_number=number, store=store)[0]
        for owner, repo, number in targets
    ]

    # Assert: only the exact seeded pull request gets the demo's waits and scope
    assert [run.fast_checks for run in runs] == [True, False, False, False]
    assert [run.seeded_head for run in runs] == ["head-sha", "", "", ""]


def _repair_on_the_second_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: RepairRun
) -> None:
    """Run the worker: the first attempt leaves CI red, the second one's commit passes."""
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "select_coding_agent", lambda: ("codex", "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(
        worker, "clone_repository", lambda _url, workspace, **_kw: Path(workspace).mkdir()
    )
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    attempts = 0

    def repair(**_kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"success": False, "error_kind": "checks_failed"}
        return {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        conclusion = "SUCCESS" if attempts == 2 else "FAILURE"
        link = run.repository_url + "/actions/runs/1"
        return {
            "state": "OPEN",
            "headRefOid": "fixed" if attempts == 2 else "broken",
            "statusCheckRollup": [{"conclusion": conclusion, "detailsUrl": link}],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "_read_pr", pr)
    worker.execute_repair(run, store)
    assert run.status is RepairStatus.SUCCEEDED


def test_a_remote_loop_records_the_failure_it_saw_and_the_repair_that_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: _RecordedEvents
) -> None:
    # Arrange
    run = _run(pr_number=1, remote=True)

    # Act
    _repair_on_the_second_attempt(tmp_path, monkeypatch, run)

    # Assert: one detection for two red reads, then the passing repair
    assert recorded.names() == ["remote_ci_failure_detected", "remote_ci_repair_succeeded"]
    on_pr = {"repair_run_id": run.id, "repository": _REPOSITORY, "demo": False, "pr_number": 1}
    _, detected = recorded.events[0]
    _, succeeded = recorded.events[1]
    assert detected == on_pr
    duration_ms = succeeded.pop("duration_ms")
    assert succeeded == {**on_pr, "attempts": 2}
    assert isinstance(duration_ms, int) and duration_ms >= 0


def test_a_loop_scheduled_from_the_shell_records_no_remote_milestone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: _RecordedEvents
) -> None:
    # Act
    _repair_on_the_second_attempt(tmp_path, monkeypatch, _run(pr_number=1))

    # Assert
    assert recorded.names() == []


def test_setup_exception_details_stay_out_of_persisted_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _RepairApi())
    monkeypatch.setattr(schedule, "get_task", lambda _id: None)

    def fail_service(**_kwargs: Any) -> None:
        raise RuntimeError("secret-provider-internal-detail")

    monkeypatch.setattr(schedule, "ensure_background_service", fail_service)
    run, _, _ = schedule.schedule_repair(owner="alice", repo="service", pr_number=1, store=store)
    assert run.status is RepairStatus.FAILED
    assert "secret-provider-internal-detail" not in render_report(store.get(run.id), tmp_path)


def test_worker_exception_details_stay_in_local_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import threading

    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run()
    store.save(run)
    monkeypatch.setattr(worker, "start_watchdog", lambda _deadline: threading.Event())

    def fail(*_args: Any) -> None:
        raise ValueError("private-provider-exception-detail")

    monkeypatch.setattr(worker, "execute_repair", fail)
    worker.run_ci_repair_worker(tmp_path, run.id)
    saved = store.get(run.id)
    assert saved.status is RepairStatus.FAILED
    assert "private-provider-exception-detail" not in render_report(saved, tmp_path)
    assert "private-provider-exception-detail" in caplog.text


def test_a_stored_fixed_repository_demo_run_stops_before_any_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A queued run of the retired demo still loads, then fails without touching GitHub."""
    import threading

    from integrations.github.tools.ci_repair_loop import worker

    # Arrange: the record as the retired demo stored it, before the seeded-demo fields
    store = RepairStore(tmp_path)
    legacy = _run().model_dump()
    legacy["demo"] = True
    legacy.pop("fast_checks")
    store.path.write_text(json.dumps({"version": 1, "runs": {legacy["id"]: legacy}}))
    monkeypatch.setattr(worker, "start_watchdog", lambda _deadline: threading.Event())

    def no_repair(*_args: Any) -> None:
        pytest.fail("A retired demo run reached the repair")

    monkeypatch.setattr(worker, "execute_repair", no_repair)

    # Act
    worker.run_ci_repair_worker(tmp_path, str(legacy["id"]))

    # Assert
    saved = store.get(str(legacy["id"]))
    assert saved.status is RepairStatus.FAILED and saved.finished_at is not None
    assert "fixed-repository demo was retired" in saved.reason


def test_wait_until_terminal_returns_when_the_run_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import tool

    store = RepairStore(tmp_path)
    run = _run(pr_number=4)
    store.save(run)
    monkeypatch.setattr(tool, "RepairStore", lambda: store)
    monkeypatch.setattr(tool, "configured_token", lambda _token: "request-token")

    class Reader:
        def request(self, *_args: Any) -> dict[str, Any]:
            return {"login": "alice", "id": 123}

    monkeypatch.setattr(tool, "GitHubRestClient", lambda _token: Reader())
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        store.save(store.get(run.id).model_copy(update={"status": RepairStatus.SUCCEEDED}))

    monkeypatch.setattr(tool.time, "sleep", sleep)

    result = tool.get_ci_repair_loop(run.id, wait_until_terminal=True)

    assert result["ok"] is True
    assert result["terminal"] is True
    assert sleeps


def test_wait_until_terminal_stops_at_the_repair_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import tool

    store = RepairStore(tmp_path)
    run = _run(pr_number=4).model_copy(update={"deadline": time.time() - 1})
    store.save(run)
    monkeypatch.setattr(tool, "RepairStore", lambda: store)
    monkeypatch.setattr(tool, "configured_token", lambda _token: "request-token")

    class Reader:
        def request(self, *_args: Any) -> dict[str, Any]:
            return {"login": "alice", "id": 123}

    monkeypatch.setattr(tool, "GitHubRestClient", lambda _token: Reader())

    def sleep(_seconds: float) -> None:
        raise AssertionError("slept past the repair deadline")

    monkeypatch.setattr(tool.time, "sleep", sleep)

    result = tool.get_ci_repair_loop(run.id, wait_until_terminal=True)

    assert result["ok"] is True
    assert result["terminal"] is False


def test_registration_publish_preserves_an_already_started_worker(tmp_path: Path) -> None:
    store = RepairStore(tmp_path)
    run, _ = store.reserve(_run())
    active = run.model_copy(update={"status": RepairStatus.RUNNING, "pr_number": 17})
    store.save(active)
    published = store.mark_registered(run.id)
    assert (
        published.registered
        and published.pr_number == 17
        and published.status is RepairStatus.RUNNING
    )


class _Clock:
    """Verification clock. Sleep is the only way it moves."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _clocked_wait(clock: _Clock, seen: dict[str, Any]) -> Any:
    from integrations.github.tools.ci_fix.verification import CheckVerification, wait_for_pr_checks

    def verify(ctx: Any, **kwargs: Any) -> CheckVerification:
        seen.update(kwargs)
        return wait_for_pr_checks(ctx, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs)

    return verify


def _demo_check_github(head: str, rollup: list[dict[str, Any]]) -> Any:
    def github(args: list[str], **_kwargs: Any) -> dict[str, Any]:
        if args[:2] == ["run", "list"]:
            return {
                "runs": [
                    {
                        "databaseId": 9,
                        "status": "completed",
                        "conclusion": "success",
                        "workflowName": "Demo calculator CI",
                    }
                ]
            }
        if args[:2] == ["pr", "view"]:
            return {"headRefOid": head, "mergeStateStatus": "CLEAN", "statusCheckRollup": rollup}
        raise AssertionError(args)

    return github


def test_seeded_demo_repository_skips_the_registration_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A seeded demo marked fast_checks returns once its check is green."""
    from integrations.github.tools.ci_repair_loop import worker

    head = "fixed-sha"
    run = _run(pr_number=1).model_copy(
        update={"repo": "opensre-ci-repair-demo-g0xd", "fast_checks": True}
    )
    passed = {
        "name": "test",
        "conclusion": "SUCCESS",
        "status": "COMPLETED",
        "workflowName": "Demo calculator CI",
        "detailsUrl": "https://github.com/alice/opensre-ci-repair-demo-g0xd/actions/runs/9",
    }
    pr = {"state": "OPEN", "headRefOid": head, "statusCheckRollup": [passed]}
    clock = _Clock()
    seen: dict[str, Any] = {}

    monkeypatch.setattr(
        "integrations.github.tools.ci_fix.verification.run_gh_json",
        _demo_check_github(head, [passed]),
    )
    monkeypatch.setattr(worker, "wait_for_pr_checks", _clocked_wait(clock, seen))
    monkeypatch.setattr(worker, "_read_pr", lambda _run, _token: pr)

    assert worker._verify_green(run, pr, "token") is True
    assert run.status is RepairStatus.SUCCEEDED
    assert seen["registration_seconds"] == 0
    assert seen["settle_seconds"] == 0
    assert seen["poll_interval_seconds"] == 2
    assert clock.sleeps == []
    assert clock.now == 0.0


def test_a_repository_named_like_the_demo_keeps_the_normal_check_wait() -> None:
    from integrations.github.tools.ci_repair_loop import worker

    run = _run(pr_number=1).model_copy(update={"repo": "opensre-ci-repair-demo-g0xd"})
    assert worker._check_wait(run) == {}


@pytest.mark.parametrize(
    ("fast_checks", "allowed_paths"),
    [(True, frozenset({"calculator.py"})), (False, None)],
)
def test_seeded_demo_repair_may_change_only_calculator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fast_checks: bool,
    allowed_paths: frozenset[str] | None,
) -> None:
    """The seeded-demo flag alone limits a repair to calculator.py.

    A repository that is only named like the demo is repaired as an ordinary PR.
    """
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run(pr_number=1).model_copy(
        update={"repo": "opensre-ci-repair-demo-g0xd", "fast_checks": fast_checks}
    )
    store.directory(run.id).mkdir()
    seen: dict[str, Any] = {}

    def repair(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        head = "fixed" if seen else "broken"
        conclusion = "SUCCESS" if seen else "FAILURE"
        return {
            "state": "OPEN",
            "headRefOid": head,
            "statusCheckRollup": [{"conclusion": conclusion}],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker, "_read_pr", pr)
    worker._repair(run, store, "test-token")

    assert seen["allowed_paths"] == allowed_paths
    assert run.checks_passed and run.fixed_sha == "fixed"


@pytest.mark.parametrize(
    ("head", "pushed", "demo"),
    [("seeded", [], True), ("own-push", ["own-push"], True), ("theirs", [], False)],
    ids=["seeded-head", "commit-this-run-pushed", "someone-elses-commit"],
)
def test_demo_only_behavior_follows_the_seeded_head_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: _RecordedEvents,
    head: str,
    pushed: list[str],
    demo: bool,
) -> None:
    """A commit from anyone else on the seeded PR gets, and is counted as, the ordinary repair."""
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run(pr_number=1).model_copy(
        update={
            "repo": "opensre-ci-repair-demo-g0xd",
            "fast_checks": True,
            "seeded_head": "seeded",
            "pushed_shas": list(pushed),
            "remote": True,
        }
    )
    store.directory(run.id).mkdir()
    seen: dict[str, Any] = {}

    def repair(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"success": True, "checks_state": "passed", "fix_head_sha": "fixed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        return {
            "state": "OPEN",
            "headRefOid": "fixed" if seen else head,
            "statusCheckRollup": [{"conclusion": "SUCCESS" if seen else "FAILURE"}],
        }

    monkeypatch.setattr(worker, "run_ci_fix", repair)
    monkeypatch.setattr(worker, "record_ci_fix_outcome", lambda _output: None)
    monkeypatch.setattr(worker, "_read_pr", pr)
    worker._repair(run, store, "test-token")

    assert seen["allowed_paths"] == (frozenset({"calculator.py"}) if demo else None)
    assert ("registration_seconds" in seen) is demo
    assert run.checks_passed and run.fast_checks is demo
    # The remote failure milestone counts the run as the demo only on the seeded chain.
    assert [(name, properties["demo"]) for name, properties in recorded.events] == [
        ("remote_ci_failure_detected", demo)
    ]


def test_demo_verification_keeps_waiting_while_checks_are_empty_or_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    head = "fixed-sha"
    run = _run(pr_number=7, fast_checks=True)
    running = {
        "name": "test",
        "conclusion": "",
        "status": "IN_PROGRESS",
        "workflowName": "Demo calculator CI",
    }
    passed = {
        "name": "test",
        "conclusion": "SUCCESS",
        "status": "COMPLETED",
        "workflowName": "Demo calculator CI",
        "detailsUrl": "https://github.com/alice/demo/actions/runs/9",
    }
    pr = {"state": "OPEN", "headRefOid": head, "statusCheckRollup": [passed]}
    clock = _Clock()
    seen: dict[str, Any] = {}
    polls = {"count": 0}

    def github(args: list[str], **kwargs: Any) -> dict[str, Any]:
        if args[:2] == ["pr", "view"]:
            polls["count"] += 1
            if polls["count"] == 1:
                rollup: list[dict[str, Any]] = []
            elif polls["count"] == 2:
                rollup = [running]
            else:
                rollup = [passed]
            return _demo_check_github(head, rollup)(args, **kwargs)
        return _demo_check_github(head, [passed])(args, **kwargs)

    def open_pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        return pr

    monkeypatch.setattr("integrations.github.tools.ci_fix.verification.run_gh_json", github)
    monkeypatch.setattr(worker, "wait_for_pr_checks", _clocked_wait(clock, seen))
    monkeypatch.setattr(worker, "_read_pr", open_pr)

    assert worker._verify_green(run, pr, "token") is True
    assert run.status is RepairStatus.SUCCEEDED
    assert polls["count"] == 3
    assert clock.sleeps == [2, 2]
    assert clock.now == 4
