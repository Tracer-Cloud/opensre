"""Shared clean-exit behavior for queued and in-flight shell controls."""

from __future__ import annotations

from rich.console import Console

from core.agent_harness import SessionCore
from infrastructure.terminal.prompt_support import print_session_resume_hint
from surfaces.interactive_shell.ui import DIM, HIGHLIGHT


def _flush_analytics_on_exit(console: Console) -> None:
    """Best-effort drain analytics before the shell process exits."""
    from infrastructure.analytics.provider import analytics_needs_flush, shutdown_analytics

    if not analytics_needs_flush():
        shutdown_analytics(flush=False)
        return

    if console.is_terminal:
        with console.status(
            f"[{DIM}]finishing up…[/]",
            spinner="dots",
            spinner_style=DIM,
        ):
            shutdown_analytics(flush=True)
    else:
        shutdown_analytics(flush=True)


def record_inflight_shell_exit(session: SessionCore, command: str) -> None:
    """Record an exit command that bypasses queued slash dispatch."""
    session.record("slash", command)


def finish_shell_exit(session: SessionCore, console: Console) -> None:
    """Render the standard resume hint and finish clean shell shutdown work."""
    # Defend against a prior inline menu that left the cursor mid-line.
    from surfaces.shared.terminal.components.choice_menu import prepare_repl_output_line

    prepare_repl_output_line()
    if session.session_id:
        console.print()
        print_session_resume_hint(console, session.session_id)
    _flush_analytics_on_exit(console)
    console.print(f"[{HIGHLIGHT}]goodbye.[/]")


__all__ = ["finish_shell_exit", "record_inflight_shell_exit"]
