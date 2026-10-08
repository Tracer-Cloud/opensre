"""Session listing slash command: /sessions."""

from __future__ import annotations

from rich.console import Console

from surfaces.interactive_shell.command_registry.session_cmds.resume import (
    resume_session_by_prefix,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.session_picker import choose_recent_session
from surfaces.interactive_shell.ui.sessions import render_recent_sessions, session_menu_items
from surfaces.shared.terminal.components.choice_menu import (
    prepare_repl_output_line,
    repl_tty_interactive,
)


def _validate_sessions_args(args: list[str]) -> str | None:
    return "usage: /sessions" if args else None


def _cmd_sessions(session: Session, console: Console, _args: list[str]) -> bool:
    from core.agent_harness.spi.defaults import default_session_repo

    entries = default_session_repo().load_recent(20)
    items = session_menu_items(
        entries,
        current_session_id=session.session_id,
        current_started_at=session.started_at,
        resumed_from_name=session.resumed_from_name,
    )
    if items and repl_tty_interactive():
        picked = choose_recent_session(items)
        if picked is None:
            session.record("slash", "/sessions")
            return True

        prepare_repl_output_line()
        if picked == session.session_id:
            console.print("Already in this session.")
            session.record("slash", "/sessions")
            return True

        slash_command = f"/sessions {picked[:8]}"
        if not resume_session_by_prefix(picked, session, console, slash_command=slash_command):
            session.record("slash", slash_command, ok=False)
        return True

    render_recent_sessions(console, items)
    session.record("slash", "/sessions")
    return True
