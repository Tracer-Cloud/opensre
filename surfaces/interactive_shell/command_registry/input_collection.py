"""Eligibility checks for missing-argument collection in terminal commands."""

from __future__ import annotations

from config.command_inputs import CommandInput
from config.interactive_override import interactive_env_value
from core.agent_harness.spi.session_state import exclusive_stdin_active
from surfaces.interactive_shell.runtime import Session
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive


def can_collect_input(session: Session, spec: CommandInput, args: list[str]) -> bool:
    """Collect only registered missing inputs while this interactive turn owns stdin."""
    return (
        spec.matches(spec.command, args)
        and interactive_env_value() != "0"
        and exclusive_stdin_active(session)
        and repl_tty_interactive()
    )
