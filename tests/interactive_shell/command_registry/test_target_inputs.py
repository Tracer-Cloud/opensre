"""Real keyboard/dispatch regressions for missing action targets."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import (
    LOOPS_TARGET_INPUT,
    WORK_DONE_INPUT,
    CommandInput,
    needs_command_input,
)
from config.constants import OPENSRE_WORK_ITEMS_DIR_ENV
from config.interactive_override import forced_non_interactive
from core.domain.work_items import add_work_item, list_work_items, work_items_path
from infrastructure.scheduling.scheduler.loops import LoopSummary, create_manual_loop
from infrastructure.scheduling.scheduler.storage import task_store
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
from surfaces.interactive_shell.command_registry import (
    dispatch_slash,
    input_collection,
    loops_cmds,
    target_collection,
)
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.target_input import build_loop_picker
from surfaces.shared.terminal.components.target_picker import TargetChoice, build_target_picker


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work"))
    monkeypatch.setattr(
        task_store, "default_task_store_path", lambda: tmp_path / "loops" / "tasks.json"
    )
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)


def run_keys(app: Any, keys: str) -> Any:
    with create_pipe_input() as pipe:
        app.input = pipe
        app.output = DummyOutput()
        return app.run(pre_run=lambda: pipe.send_text(keys))


def dispatch(
    command: str, *, tty: bool = True, own_stdin: bool = True, session: Session | None = None
) -> str:
    session = session or Session()
    session.terminal.exclusive_stdin_active = own_stdin and input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output, width=90), is_tty=tty)
    return output.getvalue()


def seed_loop(name: str = "Payment checks", *, enabled: bool = True) -> str:
    path = task_store.default_task_store_path()
    loop = create_manual_loop(
        name=name,
        prompt="Investigate checkout errors",
        time_text="08:30",
        timezone="Asia/Kolkata",
        channels=["interactive_shell"],
        store_path=path,
    )
    if not enabled:
        loop.task.enabled = False
        task_store.update_task(loop.task, path)
    return loop.task.id


@pytest.mark.parametrize("spec", [LOOPS_TARGET_INPUT, WORK_DONE_INPUT])
def test_missing_target_metadata_reserves_only_missing_targets(spec: CommandInput) -> None:
    for sub in spec.subcommands:
        for args in ([sub], [sub, ""], [sub, "   "]):
            assert needs_command_input(spec.command, args)
            assert input_policy.turn_needs_exclusive_stdin(f"{spec.command} {sub} ''", Session())
        for args in ([sub, "id"], [sub, "--help"], [sub, "--no-interactive"], [sub, "", "id"]):
            assert not needs_command_input(spec.command, args)
        assert not input_policy.turn_needs_exclusive_stdin(f"{spec.command} {sub} id", Session())


def test_positional_form_metadata_keeps_explicit_flags_direct() -> None:
    spec = CommandInput("/resource", ("edit",), max_positionals=1)
    assert spec.matches("/resource", ["edit"])
    assert spec.matches("/resource", ["edit", "resource-id"])
    assert not spec.matches("/resource", ["edit", "resource-id", "--name", "new"])
    assert not spec.matches("/resource", ["edit", "resource-id", "extra"])


def test_search_matches_hidden_descriptions_and_returns_stable_id() -> None:
    records = [
        TargetChoice("exact-id-1", "Same title", search_text="needle after hidden context"),
        TargetChoice("exact-id-2", "Same title"),
    ]
    with create_app_session(output=DummyOutput()):
        app = build_target_picker(
            records, title="/loops next", heading="Choose loop", action_label="Show next run"
        )
        assert run_keys(app, "NEEDLE\r") == "exact-id-1"
        app = build_target_picker(
            records, title="/loops next", heading="Choose loop", action_label="Show next run"
        )
        assert run_keys(app, "no match\r\x03") is None
        app = build_target_picker(
            [], title="/loops next", heading="Choose loop", action_label="Show next run"
        )
        assert run_keys(app, "invented\r\x13\x03") is None


def test_action_review_escape_returns_to_picker_then_cancels_or_reselects() -> None:
    records = [TargetChoice("first-id", "Alpha"), TargetChoice("second-id", "Beta")]
    with create_app_session(output=DummyOutput()):
        app = build_target_picker(
            records,
            title="/loops run",
            heading="Choose loop",
            action_label="Run now",
            confirmation="Run this loop now?",
        )
        assert run_keys(app, "\r\x1b\x1b") is None
        app = build_target_picker(
            records,
            title="/loops run",
            heading="Choose loop",
            action_label="Run now",
            confirmation="Run this loop now?",
        )
        assert run_keys(app, "\r\x1bBeta\r\r") == "second-id"


@pytest.mark.parametrize(
    "keys,expected", [("\r\x03", None), ("\r\r", "exact-id"), ("\r\t\r", None)]
)
def test_run_and_delete_require_separate_explicit_action(keys: str, expected: str | None) -> None:
    with create_app_session(output=DummyOutput()):
        app = build_target_picker(
            [TargetChoice("exact-id", "Loop [literal] 中", "Active · TZ UTC")],
            title="/loops run",
            heading="Choose loop",
            action_label="Run now",
            confirmation="Run this loop now?",
        )
        assert run_keys(app, keys) == expected


@pytest.mark.parametrize("command", ["/loops run", "/loops remove", "/work complete"])
def test_empty_picker_does_not_create_store_or_open_ui(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("An empty store must not open a picker")

    monkeypatch.setattr(target_collection, "run_command_input", unexpected)
    assert "No " in dispatch(command)
    assert not task_store.default_task_store_path().parent.exists()
    assert not work_items_path().parent.exists()


def test_cancelled_run_and_delete_preserve_tasks_and_never_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_loop()
    before = task_store.default_task_store_path().read_bytes()

    def collect(app: Any) -> Any:
        return run_keys(app, "\r\x03")

    def unexpected_run(*_args: Any) -> None:
        raise AssertionError("A reviewed but unconfirmed action must not execute")

    monkeypatch.setattr(target_collection, "run_command_input", collect)
    monkeypatch.setattr(loops_cmds, "_run_loop_task_ids_once", unexpected_run)
    with create_app_session(output=DummyOutput()):
        dispatch("/loops run")
        dispatch("/loops remove")
    assert task_store.default_task_store_path().read_bytes() == before


def test_run_picker_queues_selected_tasks_and_full_id_keeps_stdin_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = seed_loop("Alpha")
    second = seed_loop("Beta")
    monthly = task_store.get_task(second)
    assert monthly is not None
    monthly.cron = "0 8 1 * *"
    task_store.update_task(monthly)
    calls: list[tuple[str, ...]] = []
    captured: list[str] = []

    def collect(app: Any) -> Any:
        def capture(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is not None and not captured:
                captured.append(
                    "\n".join(
                        "".join(screen.data_buffer[y][x].char for x in range(80))
                        for y in range(screen.height)
                    )
                )

        app.after_render += capture
        return run_keys(app, "Beta\r\r")

    def run(_console: Console, task_ids: tuple[str, ...]) -> bool:
        calls.append(task_ids)
        return True

    monkeypatch.setattr(target_collection, "run_command_input", collect)
    monkeypatch.setattr(loops_cmds, "_run_loop_task_ids_once", run)
    session = Session()
    with create_app_session(output=DummyOutput()):
        dispatch("/loops run", session=session)
    assert calls == []
    assert "Cron 0 8 1 * *" in captured[0]
    assert session.terminal.pending_prompt_default == f"/loops run {second}"
    assert session.terminal.pending_prompt_autosubmit
    queued = session.terminal.pop_pending_prompt_default()
    assert not input_policy.turn_needs_exclusive_stdin(queued, session)
    dispatch(queued, session=session)
    assert calls == [(second,)]
    assert not input_policy.turn_needs_exclusive_stdin(f"/loops run {first}", Session())
    dispatch(f"/loops run {first}")
    assert calls == [(second,), (first,)]


def test_start_and_stop_aliases_offer_only_eligible_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    active = seed_loop("Active")
    stopped = seed_loop("Stopped", enabled=False)

    def collect(app: Any) -> Any:
        # An opposite-state query has no selectable row; clear it and accept the eligible one.
        return run_keys(app, "Active\r\x01\x0b\r")

    monkeypatch.setattr(target_collection, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        assert "loop started:" in dispatch("/loops enable")
    tasks = {task.id: task for task in task_store.list_tasks()}
    assert tasks[active].enabled and tasks[stopped].enabled

    def stop(app: Any) -> Any:
        return run_keys(app, "Stopped\r")

    monkeypatch.setattr(target_collection, "run_command_input", stop)
    with create_app_session(output=DummyOutput()):
        assert "loop stopped:" in dispatch("/loops pause")
    tasks = {task.id: task for task in task_store.list_tasks()}
    assert tasks[active].enabled and not tasks[stopped].enabled


def test_work_completion_picker_and_direct_multi_item_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = add_work_item(title="Fix latency", project="Payments")
    second = add_work_item(title="Fix retries", project="Platform")
    third = add_work_item(title="Fix tracing", notes="searchable context")

    def cancel(app: Any) -> Any:
        return run_keys(app, "\x03")

    before = work_items_path().read_bytes()
    monkeypatch.setattr(target_collection, "run_command_input", cancel)
    with create_app_session(output=DummyOutput()):
        dispatch("/work complete")
    assert work_items_path().read_bytes() == before

    def complete(app: Any) -> Any:
        return run_keys(app, "searchable\r")

    monkeypatch.setattr(target_collection, "run_command_input", complete)
    with create_app_session(output=DummyOutput()):
        assert "completed" in dispatch("/work complete")
    assert {item.id for item in list_work_items(status="completed")} == {third.id}
    dispatch(f"/work done {first.id} {second.id}")
    assert len(list_work_items(status="completed")) == 3


@pytest.mark.parametrize("tty,own_stdin", [(False, True), (True, False)])
def test_noninteractive_or_unowned_missing_target_keeps_usage(
    monkeypatch: pytest.MonkeyPatch, tty: bool, own_stdin: bool
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("A missing target cannot consume stdin without ownership")

    monkeypatch.setattr(target_collection, "run_command_input", unexpected)
    assert "usage:" in dispatch("/loops run", tty=tty, own_stdin=own_stdin)
    assert "usage:" in dispatch("/work done", tty=tty, own_stdin=own_stdin)
    with forced_non_interactive():
        assert "usage:" in dispatch("/loops remove")


def test_loop_picker_read_does_not_persist_legacy_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seed_loop()
    path = task_store.default_task_store_path()
    entries = json.loads(path.read_text())
    entries[0]["kind"] = "daily_summary"
    missing_work = tmp_path / "missing-work" / "work_items.json"
    reminder = dict(entries[0])
    reminder.update(
        id="legacy-reminder",
        kind="work_item_reminder",
        params={"work_item_id": "missing-item", "store_path": str(missing_work)},
    )
    entries.append(reminder)
    path.write_text(json.dumps(entries))
    original = path.read_bytes()
    monkeypatch.setattr(target_collection, "run_command_input", lambda _app: None)
    with create_app_session(output=DummyOutput()):
        dispatch("/loops run")
    assert path.read_bytes() == original
    assert not missing_work.parent.exists()


def test_cancelled_work_picker_does_not_import_legacy_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import core.domain.work_items.store as work_store

    add_work_item(title="Existing work")
    path = work_items_path()
    original = path.read_bytes()
    monkeypatch.setattr(work_store, "store_path_uses_explicit_override", lambda: False)
    monkeypatch.setattr(work_store, "OPENSRE_HOME_DIR", tmp_path)
    (tmp_path / "slack_captured_tasks.jsonl").write_text('{"text": "Legacy task"}\n')
    monkeypatch.setattr(target_collection, "run_command_input", lambda _app: None)
    dispatch("/work done")
    assert path.read_bytes() == original
    assert not (path.parent / ".legacy_slack_imported").exists()


@pytest.mark.parametrize(
    "columns,rows,action_label",
    [
        (40, 12, "Delete permanently"),
        (40, 16, "Run now"),
        (40, 20, "Delete permanently"),
        (40, 24, "Run now"),
        (40, 30, "Run now"),
        (90, 24, "Run now"),
        (160, 30, "Run now"),
    ],
)
def test_render_preserves_selected_identity_context_and_action(
    columns: int, rows: int, action_label: str
) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    captured: list[str] = []
    identifier = "12345678-1234-1234-1234-123456789012"
    cursor_rows: list[str] = []
    choice = LoopSummary(
        id=identifier,
        task_ids=(identifier,),
        name="Payment [literal] 中文 with a very long descriptive name",
        description="Check checkout errors and summarize incident risks.",
        prompt="Investigate checkout errors",
        kind=TaskKind.MANUAL_LOOP,
        cron="30 8 * * mon-fri",
        timezone="Asia/Kolkata",
        provider=Provider.INTERACTIVE_SHELL,
        chat_id="local",
        channels=("interactive_shell",),
        enabled=True,
        window_hours=24,
        last_run=None,
        next_run=None,
        schedule_error="Invalid schedule",
        mode="agent",
    )
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_loop_picker(
            [choice],
            command="/loops run",
            action_label=action_label,
            confirmation="Run this loop now with its configured delivery channels?"
            if action_label == "Run now"
            else "Permanently delete this loop and its tasks? This cannot be undone.",
            compact_confirmation="Run with configured delivery?"
            if action_label == "Run now"
            else "Cannot be undone.",
        )
        app.input = pipe

        def capture(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            ]
            if not any("Cancel" in line for line in lines):
                return
            captured.extend(lines)
            assert app.layout.current_window in screen.visible_windows
            cursor = screen.get_cursor_position(app.layout.current_window)
            cursor_rows.append(lines[cursor.y])
            pipe.send_text("\x03")

        app.after_render += capture
        assert app.run(pre_run=lambda: pipe.send_text("\r")) is None
    content = "".join(line.strip(" │") for line in captured)
    assert identifier in content
    if rows >= 16:
        assert f"/loops show {identifier}" in content
    else:
        assert "Full details: /loops show <id>" in content
    assert "Asia/Kolkata" in content
    assert "[literal]" in content and "中文" in content
    assert f"› {action_label}" in content and "Cancel" in content
    assert action_label in cursor_rows[0]
    assert (
        "configured delivery" in content
        if action_label == "Run now"
        else "Cannot be undone" in content
    )
    assert "Esc back" in content
    assert max(len(line.rstrip()) for line in captured) <= min(columns, 96)
