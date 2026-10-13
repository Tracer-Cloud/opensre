"""Agent-selected /sessions must wait for the REPL to own stdin."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.command_inputs import needs_command_input
from tools.interactive_shell.actions.slash import _slash_drives_interactive_picker


def test_agent_selected_sessions_picker_is_deferred_to_exclusive_stdin_turn() -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True, command_needs_input=needs_command_input)

    assert _slash_drives_interactive_picker(
        "/sessions", [], session=session, is_tty=True, ports=ports
    )


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/integrations", ["list"]),
        ("/mcp", ["list"]),
        ("/work", ["add", "--priority", "high"]),
        ("/goal", ["set"]),
        ("/goal", ["set", "--max-turns", "3"]),
        ("/loops", ["run"]),
        ("/loops", ["pause", ""]),
        ("/loops", ["remove"]),
        ("/work", ["complete"]),
    ],
)
def test_agent_selected_missing_input_is_deferred(command: str, args: list[str]) -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True, command_needs_input=needs_command_input)

    assert _slash_drives_interactive_picker(
        command, args, session=session, is_tty=True, ports=ports
    )


@pytest.mark.parametrize("command", ["/integrations", "/mcp"])
def test_agent_selected_bare_connection_command_is_not_a_picker(command: str) -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True, command_needs_input=needs_command_input)

    assert not _slash_drives_interactive_picker(
        command, [], session=session, is_tty=True, ports=ports
    )


def test_complete_command_and_headless_missing_input_are_not_deferred() -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True, command_needs_input=needs_command_input)
    assert not _slash_drives_interactive_picker(
        "/work", ["add", "A title"], session=session, is_tty=True, ports=ports
    )
    assert not _slash_drives_interactive_picker(
        "/work", ["add"], session=session, is_tty=False, ports=ports
    )
    assert not _slash_drives_interactive_picker(
        "/goal", ["set", "--max-turns", "3", "Ship it"], session=session, is_tty=True, ports=ports
    )
    assert not _slash_drives_interactive_picker(
        "/goal", ["set"], session=session, is_tty=False, ports=ports
    )


@pytest.mark.parametrize(
    "command,args",
    [("/loops", ["run", "loop-id"]), ("/work", ["done", "item-id"]), ("/memory", ["show"])],
)
def test_other_missing_arguments_are_not_deferred(command: str, args: list[str]) -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True, command_needs_input=needs_command_input)
    assert not _slash_drives_interactive_picker(
        command, args, session=session, is_tty=True, ports=ports
    )
