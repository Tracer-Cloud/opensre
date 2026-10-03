"""Task bookkeeping must preserve concurrent user edits and cancellation."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from core.agent_harness import pin_recurring_skill
from infrastructure.scheduling.scheduler import delivery_bundle, executor, reload_signal, runner
from infrastructure.scheduling.scheduler import tasks as task_builders
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import database, task_store
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskReport

_SYNC_TIMEOUT_SECONDS = 15.0


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: tmp_path / "tasks.json")
    monkeypatch.setattr(database, "default_run_database_path", lambda: tmp_path / "scheduler.db")
    monkeypatch.setattr(reload_signal, "_signal_path", lambda: tmp_path / "reload")


def _unexpected_agent(_payload: dict[str, Any]) -> TaskReport:
    raise AssertionError("A paused scheduler must not run an agent")


@pytest.mark.parametrize("mutation", ["disable", "reschedule", "metadata", "delete"])
def test_reload_preserves_concurrent_changes(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = task_store.add_task(
        ScheduledTask(
            id="reload-race",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="old-channel",
            params={"loop_prompt": "old prompt"},
        )
    )
    snapshot_read = threading.Event()
    resume = threading.Event()
    now = datetime(2026, 10, 3, tzinfo=UTC)
    next_run = runner.compute_next_run(original, now)
    compute_next = runner._next_run_from_trigger

    def blocked_next_run(trigger: object) -> str | None:
        snapshot_read.set()
        assert resume.wait(_SYNC_TIMEOUT_SECONDS)
        return compute_next(trigger, now)

    scheduler = BackgroundScheduler()
    scheduler.start(paused=True)
    runners = SchedulerRunners(agent=_unexpected_agent)
    try:
        assert runner.resync_scheduler_jobs(scheduler, runners) == 1
        assert scheduler.get_job(original.id) is not None
        monkeypatch.setattr(runner, "_next_run_from_trigger", blocked_next_run)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(runner.resync_scheduler_jobs, scheduler, runners)
            try:
                assert snapshot_read.wait(_SYNC_TIMEOUT_SECONDS)
                if mutation == "delete":
                    assert task_store.remove_task(original.id)
                    edited = None
                else:
                    edited = task_store.get_task(original.id)
                    assert edited is not None
                    if mutation == "disable":
                        edited.enabled = False
                    elif mutation == "reschedule":
                        edited.cron = "0 10 * * *"
                    else:
                        edited.chat_id = "new-channel"
                        edited.params = {"loop_prompt": "new prompt"}
                        edited.last_run = "2026-10-03T00:00:00+00:00"
                    assert task_store.update_task(edited)
                # The host may consume the edit signal before checking the stale snapshot.
                reload_signal.consume_scheduler_reload_request()
            finally:
                resume.set()
            pending.result(timeout=_SYNC_TIMEOUT_SECONDS)

        stored = task_store.get_task(original.id)
        if edited is None:
            assert stored is None
        else:
            assert stored is not None
            # The scheduler may update next_run, but no user-owned field.
            assert stored.model_dump(exclude={"next_run"}) == edited.model_dump(
                exclude={"next_run"}
            )
            if mutation == "reschedule":
                assert stored.next_run == compute_next(runner._make_trigger(edited), now)
            elif mutation == "metadata":
                assert stored.next_run == next_run
        if mutation in {"disable", "delete"}:
            assert scheduler.get_job(original.id) is None
        elif mutation == "reschedule":
            job = scheduler.get_job(original.id)
            assert job is not None
            assert str(job.trigger.fields[5]) == "10"
    finally:
        resume.set()
        scheduler.shutdown(wait=True)


@pytest.mark.parametrize("bookkeeping", ["repin", "rename"])
def test_skill_bookkeeping_preserves_stop_during_execution(
    bookkeeping: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, revision = pin_recurring_skill("delivering-morning-briefings")
    snapshot = task_store.add_task(
        ScheduledTask(
            id="skill-stop-race",
            kind=TaskKind.RECURRING_SKILL,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            chat_id="old-channel",
            skill_name=name if bookkeeping == "repin" else "morning-report",
            skill_revision=revision.rsplit(":", 1)[0] + ":" + "0" * 64,
        )
    )
    resolving = threading.Event()
    resume = threading.Event()
    boundary = "resolve_scheduled_skill" if bookkeeping == "repin" else "pin_recurring_skill"
    resolve = getattr(task_builders, boundary)

    def blocked_resolution(*args: str) -> Any:
        result = resolve(*args)
        resolving.set()
        assert resume.wait(_SYNC_TIMEOUT_SECONDS)
        return result

    monkeypatch.setattr(task_builders, boundary, blocked_resolution)
    deliveries: list[str] = []

    class RecordingAdapter:
        def deliver(self, task: ScheduledTask, _message: str) -> tuple[bool, str, str]:
            deliveries.append(task.chat_id)
            return True, "", "fake-message"

    monkeypatch.setattr(
        delivery_bundle,
        "_installed",
        delivery_bundle.ScheduledDeliveryAdapters({Provider.SLACK: RecordingAdapter()}),
    )

    def report(_payload: dict[str, Any]) -> TaskReport:
        return TaskReport("Report produced after the stop")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            executor.execute_task,
            snapshot,
            "2026-10-03T09:00:00Z",
            SchedulerRunners(agent=report),
        )
        try:
            assert resolving.wait(_SYNC_TIMEOUT_SECONDS)
            edited = task_store.get_task(snapshot.id)
            assert edited is not None
            edited.enabled = False
            edited.chat_id = "new-channel"
            assert task_store.update_task(edited)
        finally:
            resume.set()
        assert pending.result(timeout=_SYNC_TIMEOUT_SECONDS) is False

    stored = task_store.get_task(snapshot.id)
    assert stored is not None
    assert stored.enabled is False
    assert stored.chat_id == "new-channel"
    assert deliveries == []


def test_skill_bookkeeping_does_not_replace_a_newer_skill_selection() -> None:
    old_name, old_revision = pin_recurring_skill("delivering-morning-briefings")
    snapshot = task_store.add_task(
        ScheduledTask(
            kind=TaskKind.RECURRING_SKILL,
            cron="0 9 * * *",
            provider=Provider.SLACK,
            skill_name=old_name,
            skill_revision=old_revision,
        )
    )
    edited = task_store.get_task(snapshot.id)
    assert edited is not None
    edited.skill_name, edited.skill_revision = pin_recurring_skill("reporting-github-ci-failures")
    edited.skill_inputs = {"owner": "new-owner"}
    assert task_store.update_task(edited)

    task_builders._record_followed_revision(snapshot, "stale-resolution")

    assert task_store.get_task(snapshot.id) == edited


@pytest.mark.parametrize("persistent_failure", [False, True])
def test_startup_handles_unpersisted_legacy_migration(
    persistent_failure: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tasks.json"
    legacy = ScheduledTask(
        id="legacy-loop",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 0 1 1 *",
        provider=Provider.SLACK,
        params={"loop_prompt": "Summarize incidents"},
    ).model_dump(mode="json")
    legacy["kind"] = "custom_investigation"
    path.write_text(json.dumps([legacy]), encoding="utf-8")
    original_bytes = path.read_bytes()
    save = task_store._save_raw
    attempts = 0

    def fail_migration_write(store_path: Path, data: list[dict[str, object]]) -> None:
        nonlocal attempts
        attempts += 1
        if persistent_failure or attempts == 1:
            raise OSError("Migration write unavailable")
        save(store_path, data)

    monkeypatch.setattr(task_store, "_save_raw", fail_migration_write)
    scheduler, count = runner.start_background_scheduler(SchedulerRunners(agent=_unexpected_agent))
    try:
        assert scheduler is not None
        assert count == 1
        assert scheduler.get_job("legacy-loop") is not None
        if persistent_failure:
            assert path.read_bytes() == original_bytes
        else:
            stored = json.loads(path.read_text(encoding="utf-8"))[0]
            assert stored["kind"] == "manual_loop"
            assert stored["next_run"] is not None
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=True)
