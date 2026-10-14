"""Interactive edits preserve attached progress and durable execution metadata."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import needs_command_input
from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from core.agent_harness.spi.session_goal import SessionGoal, SessionGoalStatus, attach_session_goal
from core.domain.work_items import add_work_item, get_work_item, update_work_item
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.storage import add_task, get_task, update_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.interactive_shell.command_registry import dispatch_slash, input_collection
from surfaces.interactive_shell.command_registry import loop_edit as loop_command
from surfaces.interactive_shell.command_registry import work_edit as work_command
from surfaces.interactive_shell.command_registry.session_cmds import goal as goal_command
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.loop_edit_input import build_loop_edit_form


def _keys(app: Any, keys: str) -> Any:
    with create_pipe_input() as pipe:
        app.input = pipe
        app.output = DummyOutput()
        return app.run(pre_run=lambda: pipe.send_text(keys))


def _dispatch(command: str, *, session: Session | None = None, tty: bool = True) -> str:
    selected = session or Session()
    selected.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, selected
    )
    output = io.StringIO()
    with create_app_session(output=DummyOutput()):
        assert dispatch_slash(command, selected, Console(file=output), is_tty=tty)
    return output.getvalue()


@pytest.fixture(autouse=True)
def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)


@pytest.mark.parametrize("paused", [False, True])
def test_goal_form_preserves_budget_and_unchanged_checklist_progress(
    monkeypatch: pytest.MonkeyPatch, paused: bool
) -> None:
    session = Session()
    attached = attach_session_goal(
        session,
        SessionGoal(
            condition="1. Inspect API 2. Inspect database",
            checklist=("Inspect API", "Inspect database"),
            completed=frozenset({0}),
            turns_used=3,
            max_outer_turns=9,
            status=SessionGoalStatus.PAUSED if paused else SessionGoalStatus.ACTIVE,
        ),
    )

    def collect(app: Any) -> Any:
        return _keys(app, "\r\x01\x0b1. Inspect API 2. Inspect queue\r\x13")

    monkeypatch.setattr(goal_command, "run_command_input", collect)
    _dispatch("/goal edit", session=session)
    edited = session.session_goal
    assert edited is not None
    assert edited.condition == "1. Inspect API 2. Inspect queue"
    assert edited.completed == frozenset({0})
    assert edited.turns_used == 3 and edited.max_outer_turns == 9
    assert edited.status == attached.status
    assert session.terminal.pending_prompt_default == (None if paused else edited.condition)
    assert session.terminal.pending_prompt_autosubmit is (not paused)


def test_goal_form_cancel_and_unchanged_save_do_not_flush_or_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    initial = attach_session_goal(session, SessionGoal(condition="Current condition"))

    def unexpected(_session: Any) -> None:
        raise AssertionError("unchanged and cancelled drafts must not flush")

    monkeypatch.setattr(goal_command, "_persist_goal_state", unexpected)
    for keys in ("\r\x01\x0bDiscarded draft\x03", "\x13"):

        def collect(app: Any, input_keys: str = keys) -> Any:
            return _keys(app, input_keys)

        monkeypatch.setattr(goal_command, "run_command_input", collect)
        _dispatch("/goal edit", session=session)
        assert session.session_goal is initial
        assert session.terminal.pending_prompt_default is None


def test_work_form_patches_only_changed_fields_on_latest_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    original = add_work_item(
        title="Review API",
        priority="high",
        owner="Original owner",
        due_at="2026-10-20",
        remind_at="2026-10-19T08:00:00+05:30",
        channel_provider="slack",
        channel_id="channel-123",
        notes="Initial notes",
    )

    def collect(app: Any) -> Any:
        result = _keys(app, "\r\x01\x0bReview API and queue\r\x13")
        changed = update_work_item(
            original.id,
            changes={
                "priority": "urgent",
                "owner": "New owner",
                "notes": "A background update",
                "last_reminded_at": "2026-10-14T09:00:00+05:30",
            },
        )
        assert changed.item is not None
        return result

    monkeypatch.setattr(work_command, "run_command_input", collect)
    _dispatch(f"/work edit {original.display_id}")
    updated = get_work_item(original.id)
    assert updated is not None
    assert updated.title == "Review API and queue"
    assert updated.priority.value == "urgent"
    assert updated.owner == "New owner" and updated.notes == "A background update"
    assert updated.last_reminded_at == "2026-10-14T09:00:00+05:30"
    assert updated.remind_at == original.remind_at and updated.channel == original.channel
    assert updated.created_at == original.created_at and updated.status == original.status


def test_work_direct_edit_can_clear_optional_fields_but_invalid_values_do_not_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    original = add_work_item(
        title="Review API", project="Payments", owner="On-call", due_at="2026-10-20"
    )

    def unexpected(_app: Any) -> None:
        raise AssertionError("explicit changes must bypass interactive collection")

    monkeypatch.setattr(work_command, "run_command_input", unexpected)
    _dispatch(f'/work edit {original.id} --owner "" --due "" --priority urgent', tty=False)
    updated = get_work_item(original.id)
    assert updated is not None
    assert updated.owner == updated.due_at == ""
    assert updated.priority.value == "urgent" and updated.project == "Payments"
    _dispatch(f'/work edit {original.id} --title "Review API and queue"', tty=False)
    updated = get_work_item(original.id)
    assert updated is not None and updated.title == "Review API and queue"
    output = _dispatch(f"/work edit {original.id} --due invalid-date", tty=False)
    assert "ISO datetime" in output
    assert get_work_item(original.id) == updated
    output = _dispatch(f"/work edit {original.id} --remind-at tomorrow")
    assert "unknown option" in output
    assert get_work_item(original.id) == updated


def test_work_partial_edits_preserve_unchanged_legacy_due_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    original = add_work_item(title="Review API", due_at="tomorrow")

    def collect(app: Any) -> Any:
        return _keys(app, "\r\x01\x0bReview API and queue\r\x13")

    monkeypatch.setattr(work_command, "run_command_input", collect)
    _dispatch(f"/work edit {original.id}")
    _dispatch(f"/work edit {original.id} --priority high", tty=False)
    updated = get_work_item(original.id)
    assert updated is not None
    assert updated.title == "Review API and queue"
    assert updated.priority.value == "high" and updated.due_at == "tomorrow"


def test_flag_prefilled_missing_target_edits_require_review_before_save(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store

    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: path)
    work = add_work_item(title="API review", owner="Original owner")
    loop = add_task(
        ScheduledTask(
            name="Prompt loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * mon-fri",
            provider=Provider.INTERACTIVE_SHELL,
            enabled=False,
            params={LOOP_PROMPT_PARAM: "Review failures"},
        )
    )

    work_steps = iter(("\r", "\x13"))
    loop_steps = iter(("\r", "\x13"))

    def collect_work(app: Any) -> Any:
        current = get_work_item(work.id)
        assert current is not None and current.owner == "Original owner"
        return _keys(app, next(work_steps))

    def collect_loop(app: Any) -> Any:
        current = get_task(loop.id)
        assert current is not None and current.cron == "0 8 * * mon-fri"
        return _keys(app, next(loop_steps))

    monkeypatch.setattr(work_command, "run_command_input", collect_work)
    monkeypatch.setattr(loop_command, "run_command_input", collect_loop)
    _dispatch('/work edit --owner "New owner" --priority high')
    _dispatch("/loops edit --time 09:45")
    updated_work = get_work_item(work.id)
    updated_loop = get_task(loop.id)
    assert updated_work is not None and updated_work.owner == "New owner"
    assert updated_work.priority.value == "high"
    assert updated_loop is not None and updated_loop.cron == "45 9 * * mon-fri"
    assert updated_loop.enabled is False


def test_invalid_prefilled_loop_mode_reopens_the_choice_without_losing_changes() -> None:
    from infrastructure.scheduling.scheduler.loops import summarize_loop

    loop = summarize_loop(
        ScheduledTask(
            name="Prompt loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Review failures"},
        )
    )
    with create_app_session(output=DummyOutput()):
        draft = _keys(
            build_loop_edit_form(
                loop, prompt_editable=True, options={"name": "Updated name", "mode": "invalid"}
            ),
            "\x13\x1b[B\r\x1b\x13",
        )
    assert draft == {"name": "Updated name", "mode": "agent"}


def test_loop_form_preserves_fresh_status_delivery_and_execution_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store

    path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: path)
    task = add_task(
        ScheduledTask(
            name="Payment review",
            kind=TaskKind.MANUAL_LOOP,
            cron="30 8 * * mon-fri",
            timezone="Asia/Kolkata",
            provider=Provider.SLACK,
            chat_id="C123",
            organization="org-a",
            params={
                LOOP_PROMPT_PARAM: "Review checkout failures",
                "slack_chat_id": "C123",
                "custom_key": "retained",
            },
        )
    )

    def collect(app: Any) -> Any:
        draft = _keys(app, "\t\t\t\r\x01\x0b09:45\r\x13")
        latest = get_task(task.id)
        assert latest is not None
        latest.enabled = False
        latest.last_run = "2026-10-14T00:00:00+00:00"
        assert update_task(latest)
        return draft

    monkeypatch.setattr(loop_command, "run_command_input", collect)
    output = _dispatch(f"/loops edit {task.id}")
    assert "loop updated" in output
    updated = get_task(task.id)
    assert updated is not None
    assert updated.cron == "45 9 * * mon-fri"
    assert updated.timezone == task.timezone and updated.provider == task.provider
    assert updated.chat_id == task.chat_id and updated.organization == task.organization
    assert updated.enabled is False and updated.next_run is None
    assert updated.last_run == "2026-10-14T00:00:00+00:00"
    assert updated.params["custom_key"] == "retained"
    assert updated.params[LOOP_PROMPT_PARAM] == task.params[LOOP_PROMPT_PARAM]


def test_missing_and_cancelled_edits_do_not_create_empty_stores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store

    directory = tmp_path / "missing"
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(directory))
    monkeypatch.setattr(
        task_store, "default_task_store_path", lambda: directory / "scheduler_tasks.json"
    )
    assert "no work items" in _dispatch("/work edit")
    assert "no recurring loops" in _dispatch("/loops edit")
    assert not directory.exists()


def test_cancelled_target_and_edit_forms_leave_existing_stores_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from core.domain.work_items import work_items_path
    from infrastructure.scheduling.scheduler.storage import task_store

    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    path = tmp_path / "scheduler_tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: path)
    work = add_work_item(title="[red]API review 👩🏽‍💻[/red]")
    loop = add_task(
        ScheduledTask(
            name="Payments 東京 [b]review[/b]",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Review failures"},
        )
    )
    before_work = work_items_path().read_bytes()
    before_loops = path.read_bytes()

    def cancel(app: Any) -> Any:
        return _keys(app, "\x03")

    monkeypatch.setattr(work_command, "run_command_input", cancel)
    monkeypatch.setattr(loop_command, "run_command_input", cancel)
    for command in ("/work edit", f"/work edit {work.id}", "/loops edit", f"/loops edit {loop.id}"):
        _dispatch(command)
    assert work_items_path().read_bytes() == before_work
    assert path.read_bytes() == before_loops


@pytest.mark.parametrize("selector", ['""', '"  "'])
def test_blank_edit_targets_open_the_searchable_picker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    selector: str,
) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store

    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(
        task_store, "default_task_store_path", lambda: tmp_path / "scheduler_tasks.json"
    )
    add_work_item(title="Work item")
    add_task(
        ScheduledTask(
            name="Prompt loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Review failures"},
        )
    )
    visited: list[str] = []

    def cancel(app: Any) -> Any:
        visited.append("picker")
        return _keys(app, "\x03")

    monkeypatch.setattr(work_command, "run_command_input", cancel)
    monkeypatch.setattr(loop_command, "run_command_input", cancel)
    work_output = _dispatch(f"/work edit {selector}")
    loop_output = _dispatch(f"/loops edit {selector}")
    assert len(visited) == 2, (work_output, loop_output)


def test_loop_schedule_edit_replaces_the_shells_live_trigger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import bootstrap.adapters as adapters
    import surfaces.interactive_shell.runtime.loop_scheduler as scheduler
    from infrastructure.scheduling.scheduler.runners import SchedulerRunners
    from infrastructure.scheduling.scheduler.storage import task_store

    monkeypatch.setattr(
        task_store, "default_task_store_path", lambda: tmp_path / "scheduler_tasks.json"
    )
    monkeypatch.setattr(scheduler, "configure_process", lambda _profile: None)
    monkeypatch.setattr(
        adapters, "scheduler_runners", lambda: SchedulerRunners(agent=lambda _payload: "")
    )
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_task_count", 0)
    loop = add_task(
        ScheduledTask(
            name="Prompt loop",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * *",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Review failures"},
        )
    )
    try:
        assert scheduler.start_loop_scheduler() == 1
        original = scheduler._scheduler
        assert original.get_job(loop.id) is not None
        _dispatch(f"/loops edit {loop.id} --time 09:45", tty=False)
        current = scheduler._scheduler
        assert current is not None and current is not original
        job = current.get_job(loop.id)
        assert str(job.trigger.fields[5]) == "9"
        assert str(job.trigger.fields[6]) == "45"
    finally:
        scheduler.shutdown_loop_scheduler()


@pytest.mark.parametrize(
    "command,args",
    [
        ("/goal", ["edit"]),
        ("/work", ["edit"]),
        ("/work", ["edit", "work-123"]),
        ("/loops", ["edit"]),
        ("/loops", ["edit", "loop-123"]),
    ],
)
def test_edit_metadata_reserves_stdin_and_excludes_explicit_changes(
    command: str,
    args: list[str],
) -> None:
    assert needs_command_input(command, args)
    assert input_policy.turn_needs_exclusive_stdin(" ".join((command, *args)), Session())
    assert not needs_command_input(command, ["edit", "target", "--name", "changed"])


def test_missing_target_edit_metadata_preserves_valid_supplied_options() -> None:
    assert needs_command_input("/work", ["edit", "--priority", "high"])
    assert needs_command_input("/loops", ["edit", "--time", "09:00"])
    assert not needs_command_input("/work", ["edit", "id", "--priority", "high"])
    assert not needs_command_input("/loops", ["edit", "id", "--time", "09:00"])
    assert not needs_command_input("/work", ["edit", "--priority"])
    assert not needs_command_input("/loops", ["edit", "--unknown", "value"])


@pytest.mark.parametrize(
    "command",
    [
        "/goal edit",
        "/work edit",
        "/work edit work-123",
        "/work edit --owner On-call",
        "/loops edit",
        "/loops edit loop-123",
        "/loops edit --time 09:00",
    ],
)
def test_noninteractive_missing_edit_inputs_never_open_forms(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("noninteractive missing inputs must not collect")

    for module in (goal_command, work_command, loop_command):
        monkeypatch.setattr(module, "run_command_input", unexpected)
    _dispatch(command, tty=False)
