"""Missing-input flows through real dispatch, terminal ownership and keyboard input."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import COMMAND_INPUTS
from config.constants import OPENSRE_MEMORY_DIR_ENV, OPENSRE_WORK_ITEMS_DIR_ENV
from core.domain.memory import save_memory
from core.domain.work_items import add_work_item, list_work_items
from surfaces.interactive_shell.command_registry import (
    dispatch_slash,
    input_collection,
    memory_cmds,
    work_cmds,
)
from surfaces.interactive_shell.command_registry import help as help_cmd
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.work_input import build_work_form
from surfaces.shared.terminal.components.command_input import build_search_picker


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
        return run_keys(app, "Investigate checkout latency\r\r")

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
        return run_keys(app, "Keep this title\r\r\x1b[B\x1b[B\r\r")

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


def test_picker_filters_and_completes_only_selected_unfinished_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_work_item(title="Review documentation")
    target = add_work_item(title="Investigate checkout latency")

    def collect(app: Any) -> Any:
        return run_keys(app, "checkout\r")

    monkeypatch.setattr(work_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        dispatch("/work complete")
    completed = list_work_items(status="completed")
    assert [item.id for item in completed] == [target.id]
    assert len(list_work_items()) == 1


def test_memory_picker_opens_selected_record(monkeypatch: pytest.MonkeyPatch) -> None:
    save_memory(
        slug="prod-cluster",
        memory_type="infrastructure",
        description="Production cluster",
        body="eks-prod-1",
    )

    def collect(app: Any) -> Any:
        return run_keys(app, "cluster\r")

    monkeypatch.setattr(memory_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        assert "eks-prod-1" in dispatch("/memory show")


def test_empty_and_noninteractive_flows_never_open_input(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(_app: Any) -> None:
        raise AssertionError("must not open interactive input")

    monkeypatch.setattr(work_cmds, "run_command_input", unexpected)
    monkeypatch.setattr(memory_cmds, "run_command_input", unexpected)
    assert "No unfinished work" in dispatch("/work done")
    assert "No memories" in dispatch("/memory show")
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


def test_picker_no_match_enter_and_escape_do_not_select() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_search_picker(
            title="Select", choices=[("opaque-id", "Checkout")], action="open"
        )
        assert run_keys(app, "absent\r\x1b") is None


def test_form_requires_title_before_accepting() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({})
        result = run_keys(app, "\rValid title\r\r")
    assert result is not None
    assert result["title"] == "Valid title"


def test_work_rows_select_priority_and_existing_project_without_typing_values() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({}, projects=["payments", "platform"])
        # After the title, Create is selected. Move to Priority, choose High,
        # then select the first existing project after None.
        keys = "Checkout\r" + "\x1b[A" * 4 + "\r\x1b[B\r\x1b[B\r\x1b[B\r\x13"
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
        keys = "Checkout\r" + "\x1b[A" * 3 + "\rnew-project\x1b[B\r\r"
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
        keys = "Checkout\r" + "\x1b[A" * 2 + "\rdraft owner\x1b\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["owner"] == result["project"] == result["due"] == ""
    assert result["priority"] == "normal"


def test_reopening_supplied_project_and_custom_date_keeps_their_selection() -> None:
    with create_app_session(output=DummyOutput()):
        app = build_work_form({"project": "new project", "due": "2040-01-02"})
        # Project absent from known names remains selected; a custom date opens
        # its existing value, rather than defaulting to None and clearing it.
        keys = "Checkout\r" + "\x1b[A" * 3 + "\r\r"
        keys += "\x1b[B" * 2 + "\r\r\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    assert result["project"] == "new project"
    assert result["due"] == "2040-01-02"
