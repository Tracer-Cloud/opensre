"""Agent-selected /sessions must wait for the REPL to own stdin."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.interactive_shell.actions.slash import _slash_drives_interactive_picker


def test_agent_selected_sessions_picker_is_deferred_to_exclusive_stdin_turn() -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True)

    assert _slash_drives_interactive_picker(
        "/sessions", [], session=session, is_tty=True, ports=ports
    )


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/integrations", ["list"]),
        ("/mcp", ["list"]),
    ],
)
def test_agent_selected_connection_list_browser_is_deferred(command: str, args: list[str]) -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True)

    assert _slash_drives_interactive_picker(
        command, args, session=session, is_tty=True, ports=ports
    )


@pytest.mark.parametrize("command", ["/integrations", "/mcp"])
def test_agent_selected_bare_connection_command_is_not_a_picker(command: str) -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True)

    assert not _slash_drives_interactive_picker(
        command, [], session=session, is_tty=True, ports=ports
    )
