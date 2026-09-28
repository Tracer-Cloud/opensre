"""Session lifecycle slash commands: /clear, /new, and /compact."""

from __future__ import annotations

from rich.console import Console

from core.agent_harness import SessionManager
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, HIGHLIGHT


def _cmd_clear(session: Session, console: Console, _args: list[str]) -> bool:
    from surfaces.interactive_shell.ui import render_launch_banner

    console.clear()
    render_launch_banner(console, session=session)
    return True


def _cmd_new(session: Session, console: Console, _args: list[str]) -> bool:
    """Start a new session while preserving the current LLM conversation context.

    Unlike /clear (which only clears the screen), /new rotates the session ID
    and resets all session state while keeping cli_agent_messages and
    accumulated_context so a resumed or in-progress conversation continues
    seamlessly in a fresh session file.
    """
    saved_messages = list(session.agent.messages)
    saved_context = dict(session.accumulated_context)
    saved_resumed_name = session.resumed_from_name

    SessionManager.for_session(session).rotate_in_place(session)

    session.agent.messages = saved_messages
    session.accumulated_context = saved_context
    session.resumed_from_name = saved_resumed_name
    console.print(
        f"[{DIM}]new session started[/] [{HIGHLIGHT}]—[/] [{DIM}]conversation context carried forward.[/]"
    )
    if saved_messages:
        console.print(f"[{DIM}]  {len(saved_messages)} messages in context · type to continue[/]")
    return True


def _cmd_compact(session: Session, console: Console, _args: list[str]) -> bool:
    """Compact the live session branch and persist a compaction entry."""
    from core.agent_harness.spi.session_state import compact_session_branch

    result = compact_session_branch(session)
    if result is None:
        console.print(f"[{DIM}]Nothing to compact yet.[/]")
        return True
    console.print(
        f"[{HIGHLIGHT}]compacted session context[/] "
        f"[{DIM}]({result.before_chars} chars -> {result.after_chars} chars)[/]"
    )
    session.record("slash", "/compact")
    return True
