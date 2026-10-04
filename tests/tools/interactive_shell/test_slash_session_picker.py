"""Agent-selected /sessions must wait for the REPL to own stdin."""

from __future__ import annotations

from types import SimpleNamespace

from tools.interactive_shell.actions.slash import _slash_drives_interactive_picker


def test_agent_selected_sessions_picker_is_deferred_to_exclusive_stdin_turn() -> None:
    session = SimpleNamespace(terminal=object())
    ports = SimpleNamespace(tty_interactive=lambda: True)

    assert _slash_drives_interactive_picker(
        "/sessions", [], session=session, is_tty=True, ports=ports
    )
