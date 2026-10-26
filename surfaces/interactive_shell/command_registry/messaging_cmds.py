"""Guided messaging inputs for interactive terminals; execution stays in the CLI."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from rich.console import Console

from config.cli_command_choices import CLI_COMMAND_CHOICES
from config.command_inputs import needs_command_input
from config.interactive_override import interactive_override_env
from core.agent_harness.spi.session_state import exclusive_stdin_active, session_terminal
from integrations.messaging_security import MessagingIdentityPolicy
from integrations.store import get_integration
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.messaging_input import build_messaging_form
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand


def cmd_messaging(
    session: Session, console: Console, args: list[str], *, run_cli: Callable[..., bool]
) -> bool:
    """Collect missing terminal inputs without changing complete or noninteractive calls."""
    interactive = (
        session_terminal(session) is not None
        and exclusive_stdin_active(session)
        and not interactive_override_env()
        and repl_tty_interactive()
    )
    if interactive and not args:
        selected = repl_choose_subcommand(
            parent="/messaging", options=CLI_COMMAND_CHOICES[("/messaging",)]
        )
        if selected is None:
            return True
        args = [selected]
    if (
        interactive
        and args
        and any(args[0] == name for name, _ in CLI_COMMAND_CHOICES[("/messaging",)])
        and needs_command_input("/messaging", args)
    ):

        @cache
        def allowed_users(platform: str) -> tuple[str, ...]:
            record = get_integration(platform) if platform else None
            credentials = record.get("credentials", {}) if record else {}
            policy = MessagingIdentityPolicy.model_validate(
                credentials.get("identity_policy") or {}
            )
            return tuple(policy.allowed_user_ids)

        collected = run_command_input(build_messaging_form(args, allowed_users=allowed_users))
        if collected is None:
            return True
        args = collected
    return run_cli(console, ["messaging", *args], session=session)
