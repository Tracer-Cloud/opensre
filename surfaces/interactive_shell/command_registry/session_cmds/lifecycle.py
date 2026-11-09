"""Session lifecycle slash commands."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from core.agent_harness import SessionManager
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT


def _cmd_clear(session: Session, console: Console, _args: list[str]) -> bool:
    from surfaces.interactive_shell.ui import render_launch_banner
    from surfaces.interactive_shell.ui.poster import reset_full_screen_transcript

    if reset_full_screen_transcript(session):
        return True
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
    saved_evidence = list(session.agent.turn_evidence)
    saved_context = dict(session.accumulated_context)
    saved_resumed_name = session.resumed_from_name

    manager = SessionManager.for_session(session)
    manager.rotate_in_place(session)
    manager.carry_forward(session, messages=saved_messages, evidence=saved_evidence)

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

    result = compact_session_branch(session, manual=True)
    if result is None:
        console.print(f"[{DIM}]Nothing to compact yet.[/]")
        return True
    console.print(
        f"[{HIGHLIGHT}]compacted session context[/] "
        f"[{DIM}]({result.before_chars} chars -> {result.after_chars} chars)[/]"
    )
    session.record("slash", "/compact")
    return True


def _validate_rename_args(args: list[str]) -> str | None:
    if args == ["--reset"]:
        return None
    name = " ".join(args).strip()
    if not name or args[0] == "--reset":
        return "usage: /rename <name>  or  /rename --reset"
    return None


def _cmd_rename(session: Session, console: Console, args: list[str]) -> bool:
    name = "" if args == ["--reset"] else " ".join(args).strip()
    if not session.store.append_session_name(session.session_id, name):
        session.mark_latest(ok=False, kind="slash")
        console.print(f"[{ERROR}]could not save the session name.[/]")
        return True

    message = f"session renamed to {escape(name)}" if name else "automatic session title restored"
    console.print(f"[{HIGHLIGHT}]{message}[/]")
    return True
