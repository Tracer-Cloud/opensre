"""Schedule edits fence queued work and survive live scheduler refreshes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from config.constants.scheduler import SCHEDULER_SCHEDULE_CHANGED_AT_PARAM
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.scheduling.scheduler import executor, loops, runner, tasks
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_DESCRIPTION_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_SOURCE_PARAM,
)
from infrastructure.scheduling.scheduler.loop_edit import edit_loop
from infrastructure.scheduling.scheduler.schedule_change import obsolete_schedule_tick
from infrastructure.scheduling.scheduler.storage import (
    add_task,
    complete_run,
    database,
    get_latest_run_for_fire_time,
    get_task,
    list_tasks,
    record_run_report,
    task_store,
    try_claim,
    try_queue_run,
)
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
    TaskStatus,
)
from tests.scheduler._bundle import real_runners


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "default_run_database_path", lambda: tmp_path / "scheduler.db")
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: tmp_path / "tasks.json")


def _loop(*, cron: str = "0 8 * * *") -> ScheduledTask:
    return add_task(
        ScheduledTask(
            id="editable-loop",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron=cron,
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Original instructions"},
        )
    )


def _edit(task: ScheduledTask, changes: dict[str, str]) -> ScheduledTask:
    result, error = edit_loop((task.id,), changes, loop_id=task.id)
    assert result is not None and not error
    current = get_task(task.id)
    assert current is not None
    return current


def _old_fire() -> str:
    return runner._compute_fire_time(datetime.now(UTC) - timedelta(days=1))


@pytest.mark.parametrize("changes", [{"cron": "15 9 * * *"}, {"timezone": "America/New_York"}])
def test_reschedule_retires_pending_even_with_live_owner_and_keeps_history(
    changes: dict[str, str], tmp_path: Path
) -> None:
    task = _loop()
    completed = try_claim(task.id, "completed-tick")
    assert completed is not None and complete_run(completed, status=TaskStatus.SUCCESS)
    pending = _old_fire()
    assert try_queue_run(task.id, pending)
    report_claim = try_claim(task.id, "retained-report")
    assert report_claim is not None and record_run_report(report_claim, TaskReport("retained"))
    with sqlite3.connect(tmp_path / "scheduler.db") as conn:
        conn.execute(
            "UPDATE task_runs SET status = ? WHERE fire_time = ?",
            (TaskStatus.PENDING.value, report_claim.fire_time),
        )
    manual_key = pending.replace("Z", ".000000Z")
    assert try_queue_run(task.id, manual_key)
    unrelated_db = tmp_path / "other-host" / "scheduler.db"
    assert try_queue_run(task.id, pending, db_path=unrelated_db)
    live = try_claim(task.id, "live-tick")
    assert live is not None

    edited = _edit(task, changes)

    assert SCHEDULER_SCHEDULE_CHANGED_AT_PARAM in edited.params
    skipped = get_latest_run_for_fire_time(task.id, pending)
    assert skipped is not None and skipped.status is TaskStatus.SKIPPED
    assert skipped.error == "schedule_changed"
    assert get_latest_run_for_fire_time(task.id, "live-tick").status is TaskStatus.RUNNING
    assert get_latest_run_for_fire_time(task.id, "completed-tick").status is TaskStatus.SUCCESS
    assert get_latest_run_for_fire_time(task.id, "retained-report").status is TaskStatus.PENDING
    assert get_latest_run_for_fire_time(task.id, manual_key).status is TaskStatus.PENDING
    assert (
        get_latest_run_for_fire_time(task.id, pending, db_path=unrelated_db).status
        is TaskStatus.PENDING
    )


def test_same_schedule_and_instruction_edits_keep_queued_ticks() -> None:
    task = _loop()
    fire_time = _old_fire()
    assert try_queue_run(task.id, fire_time)
    edited = _edit(
        task,
        {
            "cron": task.cron,
            "timezone": task.timezone,
            "name": "Renamed",
            "prompt": "New instructions",
            "mode": "agent",
            "window_hours": "48",
        },
    )
    assert SCHEDULER_SCHEDULE_CHANGED_AT_PARAM not in edited.params
    assert get_latest_run_for_fire_time(task.id, fire_time).status is TaskStatus.PENDING


def test_late_admission_skips_old_tick_but_preserves_new_trigger_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _loop()
    original_save = task_store._save_raw
    obsolete = _old_fire()
    valid = runner._compute_fire_time(
        runner._make_trigger(task.model_copy(update={"cron": "15 9 * * *"})).get_next_fire_time(
            None, datetime.now(UTC) + timedelta(days=1)
        )
    )

    def _admit_after_save(path: Path, entries: list[dict[str, object]]) -> None:
        original_save(path, entries)
        assert try_queue_run(task.id, obsolete)
        assert try_queue_run(task.id, valid)

    with monkeypatch.context() as during_edit:
        during_edit.setattr(task_store, "_save_raw", _admit_after_save)
        _edit(task, {"cron": "15 9 * * *"})
    assert get_latest_run_for_fire_time(task.id, obsolete).status is TaskStatus.PENDING
    assert get_latest_run_for_fire_time(task.id, valid).status is TaskStatus.PENDING
    build = Mock(return_value="")
    monkeypatch.setattr(executor, "build_message", build)

    runner._recover_runs(real_runners())

    assert get_latest_run_for_fire_time(task.id, obsolete).status is TaskStatus.SKIPPED
    assert get_latest_run_for_fire_time(task.id, valid).status is TaskStatus.SUCCESS
    assert build.call_count == 1


def test_stale_callback_rechecks_definition_after_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _loop()
    fired_at = datetime.now(UTC) - timedelta(days=1)
    fire_time = runner._compute_fire_time(fired_at)

    def _read_then_edit(_task_id: str) -> ScheduledTask:
        _edit(task, {"cron": "15 9 * * *"})
        assert try_queue_run(task.id, fire_time)
        return task

    monkeypatch.setattr(runner, "get_task", _read_then_edit)
    build = Mock(return_value="")
    monkeypatch.setattr(executor, "build_message", build)
    runner._scheduled_job(task.id, real_runners(), scheduled_run_time=fired_at)
    skipped = get_latest_run_for_fire_time(task.id, fire_time)
    assert skipped is not None and skipped.status is TaskStatus.SKIPPED
    assert skipped.error == "schedule_changed"
    build.assert_not_called()


@pytest.mark.parametrize("retained_report", [False, True])
def test_pre_edit_claim_recovery_survives_reschedule(
    retained_report: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _loop()
    fire_time = _old_fire()
    claim = try_claim(task.id, fire_time)
    assert claim is not None
    if retained_report:
        assert record_run_report(claim, TaskReport(""))
    _edit(task, {"cron": "15 9 * * *"})
    with sqlite3.connect(tmp_path / "scheduler.db") as conn:
        conn.execute("UPDATE task_runs SET lease_expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    build = Mock(return_value="")
    monkeypatch.setattr(executor, "build_message", build)

    runner._recover_runs(real_runners())

    resumed = get_latest_run_for_fire_time(task.id, fire_time)
    assert resumed is not None and resumed.status is TaskStatus.SUCCESS and resumed.attempt == 2
    assert build.call_count == (0 if retained_report else 1)


def test_claimed_execution_and_manual_run_survive_schedule_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _loop()
    original_claim = executor.try_claim

    def _claim_then_edit(*args: object, **kwargs: object):
        claim = original_claim(*args, **kwargs)
        _edit(task, {"cron": "15 9 * * *"})
        return claim

    build = Mock(return_value="")
    monkeypatch.setattr(executor, "build_message", build)
    with monkeypatch.context() as while_claiming:
        while_claiming.setattr(executor, "try_claim", _claim_then_edit)
        assert executor.execute_task(task, _old_fire(), real_runners(), scheduled=True)
    edited = get_task(task.id)
    assert edited is not None
    # The same-second fractional manual key is deliberate work, not an old tick.
    cutoff = edited.params[SCHEDULER_SCHEDULE_CHANGED_AT_PARAM]
    manual_key = datetime.fromisoformat(cutoff).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    assert executor.execute_task(edited, manual_key, real_runners(), scheduled=True)
    assert runner.run_task_now(task.id, real_runners())
    assert build.call_count == 3


@pytest.mark.parametrize("stale_cron", ["*/15 * * * *", "0 8 * * *"])
def test_registration_preserves_concurrent_edits_and_installs_current_trigger(
    stale_cron: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _loop(cron=stale_cron)
    now = datetime.now(UTC)
    task_store.patch_task_definitions(
        (task.id,), lambda current: {"next_run": runner.compute_next_run(current, now)}
    )
    stale = list_tasks()
    saved: list[ScheduledTask] = []

    def _snapshot_then_edit() -> list[ScheduledTask]:
        saved.append(
            _edit(
                task,
                {
                    "name": "Saved edit",
                    "prompt": "Saved instructions",
                    "cron": "15 9 * * *",
                    "timezone": "America/New_York",
                },
            )
        )
        return stale

    monkeypatch.setattr(runner, "list_tasks", _snapshot_then_edit)
    scheduler = BackgroundScheduler()
    assert runner._register_jobs(scheduler, real_runners()) == 1
    current = get_task(task.id)
    assert current is not None
    assert current.name == "Saved edit" and current.params == saved[0].params
    assert current.cron == "15 9 * * *" and current.timezone == "America/New_York"
    job = scheduler.get_job(task.id)
    assert job is not None
    assert runner._next_run_from_trigger(job.trigger, now) == runner.compute_next_run(current, now)


def test_registration_reads_current_enabled_state_and_does_not_rewrite_noop_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _loop()
    scheduler = BackgroundScheduler()
    assert runner._register_jobs(scheduler, real_runners()) == 1
    store = tmp_path / "tasks.json"
    before = (store.read_bytes(), store.stat().st_mtime_ns)
    assert runner._register_jobs(scheduler, real_runners()) == 1
    assert (store.read_bytes(), store.stat().st_mtime_ns) == before
    stale = list_tasks()
    task_store.patch_task_definitions((task.id,), lambda _current: {"enabled": False})
    monkeypatch.setattr(runner, "list_tasks", lambda: stale)
    disabled_scheduler = BackgroundScheduler()
    assert runner._register_jobs(disabled_scheduler, real_runners()) == 0
    assert disabled_scheduler.get_job(task.id) is None


def test_schedule_cutoff_does_not_change_readd_identity() -> None:
    task = _loop()
    edited = _edit(task, {"cron": "15 9 * * *"})
    params = {
        key: value
        for key, value in edited.params.items()
        if key != SCHEDULER_SCHEDULE_CHANGED_AT_PARAM
    }
    repeated = add_task(edited.model_copy(update={"id": "another-id", "params": params}))
    assert repeated.id == task.id and len(list_tasks()) == 1


def test_scoped_shell_refresh_keeps_service_jobs_and_edit_authority(tmp_path: Path) -> None:
    task = _loop()
    task_store.update_task(task.model_copy(update={"organization": "org-a"}))
    other = add_task(task.model_copy(update={"id": "other-org", "organization": "org-b"}))
    scope = StorageScope(principal=Principal.org("org-a"), actor=Actor(id="actor-a"))
    scheduler = BackgroundScheduler()
    with bound_storage_scope(scope):
        assert runner._register_jobs(scheduler, real_runners()) == 2
        assert scheduler.get_job(task.id) is not None and scheduler.get_job(other.id) is not None
        before = (tmp_path / "tasks.json").read_bytes()
        rejected, error = edit_loop((other.id,), {"name": "Unauthorized"}, loop_id=other.id)
        assert rejected is None and "not available in this organization" in error
        with pytest.raises(ValueError, match="cannot change task configuration"):
            task_store.patch_scheduler_task(other.id, lambda _current: {"name": "Unauthorized"})
        assert (tmp_path / "tasks.json").read_bytes() == before


def test_retirement_failure_keeps_durable_cutoff_and_requests_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _loop()
    fire_time = _old_fire()
    assert try_queue_run(task.id, fire_time)
    reload = Mock()
    monkeypatch.setattr(task_store.reload_signal, "request_scheduler_reload", reload)
    with monkeypatch.context() as failed_retirement:
        failed_retirement.setattr(
            task_store, "retire_pending_runs", Mock(side_effect=sqlite3.OperationalError("locked"))
        )
        edited = _edit(task, {"cron": "15 9 * * *"})
    reload.assert_called_once()
    assert SCHEDULER_SCHEDULE_CHANGED_AT_PARAM in edited.params
    build = Mock(return_value="")
    monkeypatch.setattr(executor, "build_message", build)
    runner._recover_runs(real_runners())
    assert get_latest_run_for_fire_time(task.id, fire_time).status is TaskStatus.SKIPPED
    build.assert_not_called()


@pytest.mark.parametrize("rename", [False, True])
def test_skill_pin_progress_preserves_edits_and_later_explicit_pins(
    rename: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = add_task(
        ScheduledTask(
            id="recurring-skill",
            kind=TaskKind.RECURRING_SKILL,
            name="Original",
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            skill_name="morning-report" if rename else "delivering-morning-briefings",
            skill_revision="old-pin",
        )
    )
    edited = _edit(stale, {"name": "Saved edit", "cron": "15 9 * * *"})

    def _advance_pin(snapshot: ScheduledTask) -> None:
        if rename:
            tasks._migrate_renamed_skill(snapshot)
        else:
            tasks._record_followed_revision(snapshot, "followed-pin")

    monkeypatch.setattr(
        tasks, "pin_recurring_skill", lambda _name: ("delivering-morning-briefings", "followed-pin")
    )
    _advance_pin(stale)
    saved = get_task(stale.id)
    assert saved is not None
    assert saved.name == edited.name and saved.cron == edited.cron and saved.params == edited.params
    assert saved.skill_revision == stale.skill_revision == "followed-pin"
    assert saved.skill_name == stale.skill_name == "delivering-morning-briefings"
    prior_snapshot = stale.model_copy()
    task_store.update_task(saved.model_copy(update={"skill_revision": "explicit-pin"}))
    _advance_pin(prior_snapshot)
    assert get_task(stale.id).skill_revision == "explicit-pin"


@pytest.mark.parametrize("enabled", [False, True])
def test_enable_toggle_uses_latest_schedule_and_stop_cleans_queued_ticks(
    enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = _loop()
    original_resolve = loops.resolve_loop_summary
    saved: list[ScheduledTask] = []
    queued: list[str] = []

    def _select_then_edit(*args: object, **kwargs: object):
        selected = original_resolve(*args, **kwargs)
        edited = _edit(task, {"name": "Saved edit", "cron": "15 9 * * *"})
        saved.append(edited)
        fire = runner._make_trigger(edited).get_next_fire_time(None, datetime.now(UTC))
        queued.append(runner._compute_fire_time(fire))
        assert try_queue_run(task.id, queued[0])
        return selected

    monkeypatch.setattr(loops, "resolve_loop_summary", _select_then_edit)
    result, error = loops.set_loop_enabled(task.id, enabled=enabled)
    assert result is not None and not error
    current = get_task(task.id)
    assert current is not None and current.enabled is enabled
    assert current.name == saved[0].name and current.cron == saved[0].cron
    assert current.params == saved[0].params
    assert current.next_run == (runner.compute_next_run(current) if enabled else None)
    assert get_latest_run_for_fire_time(task.id, queued[0]).status is (
        TaskStatus.PENDING if enabled else TaskStatus.SKIPPED
    )


@pytest.mark.parametrize("changed_prompt", [False, True])
def test_starter_upgrade_rechecks_latest_prompt_and_keeps_schedule_edits(
    changed_prompt: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    starter = next(starter for starter in loops.STARTER_LOOPS if starter.skill_name)
    stale = add_task(
        ScheduledTask(
            id="legacy-starter",
            name="Original",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            enabled=False,
            provider=Provider.INTERACTIVE_SHELL,
            params={
                LOOP_PROMPT_PARAM: loops._LEGACY_MORNING_REPORT_PROMPT,
                LOOP_SOURCE_PARAM: loops._ONBOARDING_LOOP_SOURCE,
            },
        )
    )
    changes = {"name": "Saved edit", "cron": "15 9 * * *"}
    if changed_prompt:
        changes["prompt"] = "Custom instructions"
    edited = _edit(stale, changes)
    monkeypatch.setattr(loops, "pin_recurring_skill", lambda _name: (starter.skill_name, "new-pin"))
    migrated = loops._upgrade_legacy_skill_starter(stale, starter, store_path=None)
    current = get_task(stale.id)
    assert current is not None
    assert current.name == edited.name and current.cron == edited.cron
    assert (
        current.params[SCHEDULER_SCHEDULE_CHANGED_AT_PARAM]
        == edited.params[SCHEDULER_SCHEDULE_CHANGED_AT_PARAM]
    )
    if changed_prompt:
        assert migrated is None and current.model_dump() == edited.model_dump()
    else:
        assert migrated is not None and current.kind is TaskKind.RECURRING_SKILL
        assert current.params[LOOP_DESCRIPTION_PARAM] == starter.description
        assert LOOP_PROMPT_PARAM not in current.params


def test_current_trigger_accepts_both_dst_fold_ticks_and_rejects_old_slot() -> None:
    task = ScheduledTask(
        kind=TaskKind.MANUAL_LOOP,
        cron="30 2 * * *",
        timezone="Europe/Berlin",
        provider=Provider.INTERACTIVE_SHELL,
        params={SCHEDULER_SCHEDULE_CHANGED_AT_PARAM: "2026-10-24T00:00:00+00:00"},
    )
    assert not obsolete_schedule_tick(task, "2026-10-25T00:30:00Z")
    assert not obsolete_schedule_tick(task, "2026-10-25T01:30:00Z")
    assert obsolete_schedule_tick(task, "2026-10-25T01:45:00Z")
