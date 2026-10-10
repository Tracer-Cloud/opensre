"""Missing-input flows through real dispatch, terminal ownership and keyboard input."""

from __future__ import annotations

import asyncio
import io
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import COMMAND_INPUTS
from config.constants import (
    OPENSRE_INTERACTIVE_ENV,
    OPENSRE_MEMORY_DIR_ENV,
    OPENSRE_WORK_ITEMS_DIR_ENV,
)
from config.repl_config import ReplConfig
from core.domain.work_items import list_work_items
from surfaces.interactive_shell.command_registry import (
    dispatch_slash,
    input_collection,
    work_cmds,
)
from surfaces.interactive_shell.command_registry import help as help_cmd
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.work_input import build_work_form


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_WORK_ITEMS_DIR_ENV, str(tmp_path / "work"))
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)


def run_keys(app: Any, keys: str) -> Any:
    """Drive the actual prompt-toolkit bindings with a deterministic input pipe."""
    with create_pipe_input() as pipe:
        app.input = pipe
        app.output = DummyOutput()
        return app.run(pre_run=lambda: pipe.send_text(keys))


def dispatch(command: str, *, tty: bool = True) -> str:
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output), is_tty=tty)
    return output.getvalue()


@pytest.mark.parametrize("via_help", [False, True])
def test_work_form_preserves_options_and_persists_once(
    monkeypatch: pytest.MonkeyPatch, via_help: bool
) -> None:
    def collect(app: Any) -> Any:
        return run_keys(app, "Investigate checkout latency\r\x13")

    monkeypatch.setattr(work_cmds, "run_command_input", collect)
    command = '/work add --project "Payments API" --priority high --owner Anwesh --due 2026-10-12'
    if via_help:
        monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(help_cmd, "browse_help_commands", lambda _sections: "/work")
        monkeypatch.setattr(help_cmd, "repl_choose_subcommand", lambda **_kw: "add")
        command = "/help"
    with create_app_session(output=DummyOutput()):
        dispatch(command)
    items = list_work_items(status=None)
    assert len(items) == 1
    assert items[0].title == "Investigate checkout latency"
    if not via_help:
        assert (items[0].project, items[0].priority.value, items[0].owner, items[0].due_at) == (
            "Payments API",
            "high",
            "Anwesh",
            "2026-10-12",
        )


def test_work_form_validation_retains_title_and_cancellation_is_nonmutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect(app: Any) -> Any:
        # Invalid supplied priority opens the selector; choose High without retyping the title.
        return run_keys(app, "Keep this title\r\x13\x1b[B\x1b[B\r\x13")

    monkeypatch.setattr(work_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        dispatch("/work add --priority invalid")
    assert list_work_items()[0].title == "Keep this title"

    def cancel(app: Any) -> Any:
        return run_keys(app, "Unsaved draft\x03")

    monkeypatch.setattr(work_cmds, "run_command_input", cancel)
    with create_app_session(output=DummyOutput()):
        dispatch("/work add")
    assert len(list_work_items(status=None)) == 1


def test_complete_and_noninteractive_flows_never_open_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("must not open interactive input")

    monkeypatch.setattr(work_cmds, "run_command_input", unexpected)
    assert "usage:" in dispatch("/work add", tty=False)
    dispatch("/work add Fully specified --priority high")
    assert len(list_work_items()) == 1
    session = Session()
    output = io.StringIO()
    dispatch_slash("/work add", session, Console(file=output), is_tty=True)
    assert "usage:" in output.getvalue()


def test_all_registered_inputs_reserve_stdin() -> None:
    for spec in COMMAND_INPUTS:
        for subcommand in spec.subcommands:
            assert input_policy.turn_needs_exclusive_stdin(
                f"{spec.command} {subcommand}", Session()
            )


def test_form_requires_title_before_accepting() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        result = run_keys(app, "\rValid title\r\x13")
    assert result is not None
    assert result["title"] == "Valid title"


def test_work_rows_select_priority_and_existing_project_without_typing_values() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({}, projects=["payments", "platform"])
        # After the title, Priority is selected. Open it and choose High,
        # then select the first existing project after None.
        keys = "Checkout\r\r\x1b[B\r\x1b[B\r\x1b[B\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result == {
        "title": "Checkout",
        "priority": "high",
        "project": "payments",
        "owner": "",
        "due": "",
    }


def test_work_project_custom_and_date_validation_preserve_other_fields() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({}, projects=["payments"])
        # Search has no existing match: select New after None, preserving query.
        keys = "Checkout\r\x1b[B\rnew-project\x1b[B\r\r"
        # Due: select Custom, reject invalid date, correct it, then save.
        keys += "\x1b[B" * 2 + "\r" + "\x1b[B" * 3 + "\rbad-date\r\x01\x0b2026-10-12\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["project"] == "new-project"
    assert result["due"] == "2026-10-12"
    assert result["title"] == "Checkout"


def test_work_edit_back_discards_draft_and_optional_fields_stay_unset() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        keys = "Checkout\r" + "\x1b[B" * 2 + "\rdraft owner\x1b\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["owner"] == result["project"] == result["due"] == ""
    assert result["priority"] == "normal"


def test_reopening_supplied_project_and_custom_date_keeps_their_selection() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({"project": "new project", "due": "2040-01-02"})
        # Project absent from known names remains selected; a custom date opens
        # its existing value, rather than defaulting to None and clearing it.
        keys = "Checkout\r\x1b[B\r\r"
        keys += "\x1b[B" * 2 + "\r\r\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["project"] == "new project"
    assert result["due"] == "2040-01-02"


def test_work_search_and_relative_date_choices_preserve_values() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({}, projects=["payments", "platform"], today=date(2026, 10, 10))
        # Filter an existing project, then choose Tomorrow from the date presets.
        keys = "Checkout\r\x1b[B\rPAY\r" + "\x1b[B" * 2 + "\r" + "\x1b[B" * 2 + "\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["project"] == "payments"
    assert result["due"] == "2026-10-11"
    assert result["title"] == "Checkout"


def test_work_escape_from_initial_empty_title_cancels() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        assert run_keys(app, "\x1bShould not save\r\x13\x03") is None


@pytest.mark.parametrize("columns,rows", [(100, 30), (45, 12)])
def test_work_form_spacing_keeps_selected_action_visible(columns: int, rows: int) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    captured: list[str] = []
    selected_rows: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_work_form({})
        app.input = pipe

        def capture_summary(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            ]
            if not any("Create work item" in line for line in lines):
                return
            captured.extend(lines)
            cursor = screen.get_cursor_position(app.layout.current_window)
            selected_rows.append(lines[cursor.y])
            pipe.send_text("\x13")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def start() -> None:
            app.create_background_task(stop_if_stalled())
            pipe.send_text("Spacing check\r" + "\t" * 4)

        app.after_render += capture_summary
        result = app.run(pre_run=start)

    assert captured, "Work summary did not render before the timeout"
    assert "Create work item" in selected_rows[0]
    assert result is not None and result["title"] == "Spacing check"
    assert any("Cancel" in line for line in captured)
    title_line = next(line for line in captured if "Title" in line)
    assert title_line.index("Title") >= 5
    due_row = next(index for index, line in enumerate(captured) if "Due" in line)
    create_row = next(index for index, line in enumerate(captured) if "Create work item" in line)
    assert create_row == due_row + 2
    assert len(captured) <= rows


def test_title_confirmation_does_not_create_on_a_second_enter() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        assert run_keys(app, "Review latency\r\r\x03") is None


def test_tab_navigation_reaches_create_from_priority() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        # Move to Project and back to Priority, then tab through to Create.
        result = run_keys(app, "Review latency\r\t\x1b[Z" + "\t" * 4 + "\r\x03")
    assert result is not None
    assert result["title"] == "Review latency"
    assert result["priority"] == "normal"


@pytest.mark.parametrize("command", ["/work done", "/work complete", "/memory show"])
def test_other_missing_arguments_keep_usage_response(command: str) -> None:
    assert "usage:" in dispatch(command)


@pytest.mark.parametrize("tty", [True, False])
def test_cli_interactive_override_still_respects_noninteractive_dispatch(
    monkeypatch: pytest.MonkeyPatch, tty: bool
) -> None:
    monkeypatch.setenv(OPENSRE_INTERACTIVE_ENV, "0")
    assert ReplConfig.load(cli_enabled=True).enabled

    def collect(app: Any) -> Any:
        assert tty, "Noninteractive dispatch must not open the form"
        return run_keys(app, "Override regression\r\x13")

    monkeypatch.setattr(work_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        output = dispatch("/work add --priority high --project payments", tty=tty)
    items = list_work_items()
    if tty:
        assert len(items) == 1
        assert items[0].title == "Override regression"
        assert items[0].priority.value == "high"
        assert items[0].project == "payments"
    else:
        assert "usage:" in output
        assert not items
