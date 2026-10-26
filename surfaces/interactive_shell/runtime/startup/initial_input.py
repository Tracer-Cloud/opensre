"""Non-interactive initial-input replay for REPL startup."""

from __future__ import annotations

from rich.console import Console

from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from surfaces.interactive_shell.ui.terminal_ui import render_terminal_ui


def run_initial_input(
    initial_input: str,
    session: Session,
    console: Console | None = None,
) -> int:
    # Imported lazily so importing this module during REPL boot (main.py imports
    # ``run_initial_input`` at top) does not pull the harness/turn-execution
    # stack into the base import path when there is no initial input to replay.
    from surfaces.interactive_shell.runtime.shell_turn_execution import execute_shell_turn

    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_terminal_ui(console)
    for line in initial_input.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        render_submitted_prompt(console, session, stripped)
        with (
            bound_usage_context(
                surface=UsageSurface.CLI,
                session_id=session.session_id,
            ),
        ):
            execute_shell_turn(
                stripped,
                session,
                console,
                confirm_fn=None,
                is_tty=False,
            )
    return 0


__all__ = ["run_initial_input"]
