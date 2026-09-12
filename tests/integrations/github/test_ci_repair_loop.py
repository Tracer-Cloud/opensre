"""Durable demo identity, fixture authorization, deadline, and retained evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from typing import Any

import psutil
import pytest
from filelock import FileLock

from config.constants.ci_repair import CI_REPAIR_FINISH_RESERVE_SECONDS
from config.constants.github import GITHUB_CI_DEMO_REPOSITORY
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_repair_loop import fixture, schedule, supervisor
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.report import render_report
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def _run(run_id: str = "a" * 12, **kwargs: Any) -> RepairRun:
    now = time.time()
    return RepairRun(
        id=run_id,
        owner="alice",
        actor="alice",
        actor_id=123,
        repo=GITHUB_CI_DEMO_REPOSITORY,
        demo=True,
        started_at=now,
        deadline=now + 600,
        **kwargs,
    )


def _task(run: RepairRun) -> ScheduledTask:
    return ScheduledTask(
        id=run.id,
        kind=TaskKind.MANUAL_LOOP,
        cron="* * * * *",
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


def _git_sha(content: str) -> str:
    blob = content.encode()
    return hashlib.sha1(f"blob {len(blob)}\0".encode() + blob, usedforsecurity=False).hexdigest()


class _GitHub:
    """API-shaped repository/git data with independent refs and immutable trees."""

    def __init__(self, *, files: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.repository: dict[str, Any] | None = None
        self.trees = {"tree0": files or {"README.md": "Demo\n"}}
        self.commits = {"commit0": "tree0"}
        self.refs = {"main": "commit0"}
        self.prs: list[dict[str, Any]] = []
        if files is not None:
            self._create()

    def _create(self) -> dict[str, Any]:
        self.repository = {
            "id": 42,
            "full_name": f"alice/{GITHUB_CI_DEMO_REPOSITORY}",
            "default_branch": "main",
            "private": True,
            "fork": False,
            "permissions": {"push": True},
        }
        return self.repository

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        route = path.removeprefix(f"repos/alice/{GITHUB_CI_DEMO_REPOSITORY}")
        body = kwargs.get("body", {})
        if path == "user":
            return {"login": "alice", "id": 123}
        if path == "user/repos":
            assert self.repository is None
            return self._create()
        if route == "":
            if self.repository is None:
                raise GitHubApiError("Not Found", HTTPStatus.NOT_FOUND)
            return self.repository
        if route == "/contents":
            return [{"name": name} for name in self.trees[self.commits[self.refs["main"]]]]
        if route.startswith("/branches/"):
            return {"commit": {"sha": self.refs[route.removeprefix("/branches/")]}}
        if route.startswith("/git/commits/"):
            return {"tree": {"sha": self.commits[route.rsplit("/", 1)[1]]}}
        if route.startswith("/git/trees/"):
            sha = route.rsplit("/", 1)[1]
            return {
                "truncated": False,
                "tree": [
                    {"path": name, "sha": _git_sha(content), "mode": "100644", "type": "blob"}
                    for name, content in self.trees[self.commits[sha]].items()
                ],
            }
        if route == "/git/trees":
            files = dict(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                files[entry["path"]] = entry["content"]
            sha = f"tree{len(self.trees)}"
            self.trees[sha] = files
            return {"sha": sha}
        if route == "/git/commits":
            sha = f"commit{len(self.commits)}"
            self.commits[sha] = body["tree"]
            return {"sha": sha}
        if route.startswith("/git/ref/heads/"):
            branch = route.removeprefix("/git/ref/heads/")
            if branch not in self.refs:
                raise GitHubApiError("Not Found", HTTPStatus.NOT_FOUND)
            return {"object": {"sha": self.refs[branch]}}
        if route.startswith("/git/refs/heads/"):
            branch = route.removeprefix("/git/refs/heads/")
            if method == "DELETE":
                del self.refs[branch]
            else:
                assert body["force"] is False
                self.refs[branch] = body["sha"]
            return {}
        if route == "/git/refs":
            branch = body["ref"].removeprefix("refs/heads/")
            assert branch not in self.refs
            self.refs[branch] = body["sha"]
            return {}
        if route == "/pulls":
            if method == "GET":
                return [
                    pr
                    for pr in self.prs
                    if f"alice:{pr['head']}" == kwargs["params"]["head"] and pr["state"] == "open"
                ]
            pr = {**body, "number": len(self.prs) + 1, "state": "open"}
            self.prs.append(pr)
            return pr
        if route.startswith("/pulls/"):
            self.prs[int(route.rsplit("/", 1)[1]) - 1].update(body)
            return {}
        raise AssertionError((method, path, kwargs))


def test_demo_creates_once_reuses_baseline_and_recovers_same_branch(tmp_path: Path) -> None:
    api = _GitHub()
    store = RepairStore(tmp_path)
    first = _run()
    fixture.prepare_demo(api, first, store)
    baseline = api.refs["main"]
    assert api.trees[api.commits[baseline]]["calculator.py"].endswith("a + b\n")
    fixture.prepare_demo(api, store.get(first.id), store)
    assert len(api.prs) == 1
    second = _run("b" * 12)
    fixture.prepare_demo(api, second, store)
    assert api.refs["main"] == baseline
    assert first.branch != second.branch and first.pr_number != second.pr_number
    assert sum(path == "user/repos" for _, path, _ in api.calls) == 1
    first.checks_passed = True
    fixture.cleanup_demo(api, first)
    assert first.branch not in api.refs and second.branch in api.refs
    assert api.repository is not None and api.refs["main"] == baseline
    assert api.prs[0]["state"] == "closed"


def test_unmarked_or_modified_repository_is_never_adopted(tmp_path: Path) -> None:
    api = _GitHub(files={"calculator.py": "unrelated work"})
    with pytest.raises(ValueError, match="unrecognized or modified"):
        fixture.prepare_demo(api, _run(), RepairStore(tmp_path))
    assert all(method == "GET" for method, _, _ in api.calls)


def test_fixture_has_a_real_failing_test_and_one_pr_workflow(tmp_path: Path) -> None:
    for name, content in fixture.baseline_files("main").items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    good = subprocess.run(
        [sys.executable, "-m", "unittest", "-v"], cwd=tmp_path, capture_output=True
    )
    assert good.returncode == 0
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    # Remove the bytecode cache: equal-sized writes can otherwise share its timestamp.
    import shutil

    shutil.rmtree(tmp_path / "__pycache__")
    bad = subprocess.run(
        [sys.executable, "-m", "unittest", "-v"], cwd=tmp_path, capture_output=True
    )
    assert bad.returncode != 0 and b"AssertionError" in bad.stderr


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


def test_schedule_reuses_active_run_instead_of_resetting_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    tasks: dict[str, ScheduledTask] = {}
    api = _GitHub()
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: api)
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    monkeypatch.setattr(schedule, "get_task", tasks.get)

    def add(task: ScheduledTask) -> ScheduledTask:
        tasks[task.id] = task
        return task

    monkeypatch.setattr(schedule, "add_task", add)
    first, reused, _ = schedule.schedule_repair(demo=True, store=store)
    second, reused_again, _ = schedule.schedule_repair(demo=True, store=store)
    assert not reused and reused_again
    assert first.id == second.id and first.deadline == second.deadline
    assert len(tasks) == 1 and next(iter(tasks.values())).cron == "* * * * *"


def test_failure_retains_diagnostics_and_report_contains_evidence_links(tmp_path: Path) -> None:
    run = _run(pr_number=7).model_copy(
        update={
            "status": RepairStatus.FAILED,
            "attempts": 4,
            "failed_run_url": "https://github.com/alice/demo/actions/runs/10",
            "reason": "CI remains failing.",
        }
    )
    api = _GitHub()
    fixture.cleanup_demo(api, run)
    assert not api.calls
    report = render_report(run, tmp_path)
    assert all(line.startswith("- ") for line in report.splitlines())
    assert run.pr_url in report and run.failed_run_url in report
    assert "**Repair attempts:** 4" in report and "Temporary artifacts retained" in report


def test_extra_workflow_in_marked_repo_is_refused(tmp_path: Path) -> None:
    files = fixture.baseline_files("main")
    files[".github/workflows/deploy.yml"] = "name: unrelated deployment"
    api = _GitHub(files=files)
    with pytest.raises(ValueError, match="unrecognized or modified"):
        fixture.prepare_demo(api, _run(), RepairStore(tmp_path))
    assert all(method == "GET" for method, _, _ in api.calls)


@pytest.mark.parametrize("changed_head", [False, True])
def test_worker_retries_then_cleans_only_the_verified_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_head: bool,
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    store = RepairStore(tmp_path)
    run = _run()
    api = _GitHub()
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "verify_coding_agent", lambda: (True, "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: api)

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
        if calls <= 4:
            return {"success": False, "error_kind": "checks_failed"}
        return {"success": True, "checks_state": "passed", "pushed_head_sha": "fixed"}

    def pr(_run: RepairRun, _token: str) -> dict[str, Any]:
        finished = calls == 5
        return {
            "state": "OPEN",
            "headRefOid": "someone-else" if finished and changed_head else "fixed",
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
    assert run.attempts == 5  # No old three-attempt ceiling.
    if changed_head:
        assert run.status is RepairStatus.FAILED and not run.checks_passed
        assert api.prs[0]["state"] == "open" and run.branch in api.refs
        assert Path(run.workspace).exists()
    else:
        assert run.status is RepairStatus.SUCCEEDED and run.checks_passed
        assert run.fixed_sha == "fixed" and run.passed_run_url
        assert api.prs[0]["state"] == "closed" and run.branch not in api.refs
        assert not Path(run.workspace).exists()


def test_account_change_stops_before_any_remote_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from integrations.github.tools.ci_repair_loop import worker

    api = _GitHub()
    monkeypatch.setattr(worker, "configured_token", lambda: "test-token")
    monkeypatch.setattr(worker, "verify_coding_agent", lambda: (True, "ready"))
    monkeypatch.setattr(worker, "GitHubRestClient", lambda _token: api)
    run = _run().model_copy(update={"actor_id": 456})

    def refuse_clone(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("An unauthorized worker must not reach a repository checkout.")

    monkeypatch.setattr(worker, "clone_repository", refuse_clone)
    with pytest.raises(ValueError, match="account changed"):
        worker.execute_repair(run, RepairStore(tmp_path))
    assert [(method, path) for method, path, _ in api.calls] == [("GET", "user")]


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
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _GitHub())
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)
    run, _, _ = schedule.schedule_repair(demo=True, store=store)
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

    run = _run(pr_number=1).model_copy(update={"demo": False})
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
        return CheckVerification(state=CheckState.FAILED, check_names=("late security",))

    monkeypatch.setattr(worker, "wait_for_pr_checks", verify, raising=False)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: None)
    worker._repair(run, RepairStore(tmp_path), "test-token")
    assert checked == ["green-head"]
    assert run.status is RepairStatus.CANCELLED


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


def test_interrupted_registration_recovers_original_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    original, _ = store.reserve(_run().model_copy(update={"actor": "old-alice"}))
    tasks: dict[str, ScheduledTask] = {}
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _GitHub())
    monkeypatch.setattr(schedule, "get_task", tasks.get)
    monkeypatch.setattr(schedule, "ensure_background_service", lambda **_kw: None)

    def add(task: ScheduledTask) -> ScheduledTask:
        tasks[task.id] = task
        return task

    monkeypatch.setattr(schedule, "add_task", add)
    resumed, reused, _ = schedule.schedule_repair(demo=True, store=store)
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


def test_setup_exception_details_stay_out_of_persisted_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RepairStore(tmp_path)
    monkeypatch.setattr(schedule, "configured_token", lambda _token: "test-token")
    monkeypatch.setattr(schedule, "GitHubRestClient", lambda _token: _GitHub())
    monkeypatch.setattr(schedule, "get_task", lambda _id: None)

    def fail_service(**_kwargs: Any) -> None:
        raise RuntimeError("secret-provider-internal-detail")

    monkeypatch.setattr(schedule, "ensure_background_service", fail_service)
    run, _, _ = schedule.schedule_repair(demo=True, store=store)
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
