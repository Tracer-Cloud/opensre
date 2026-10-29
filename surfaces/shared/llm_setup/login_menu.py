"""Provider connection actions in the shared terminal subcommand tray."""

from __future__ import annotations

from config.cli_command_choices import CLI_COMMAND_CHOICES
from surfaces.shared.llm_setup.auth_profiles import iter_auth_profiles
from surfaces.shared.llm_setup.auth_service import provider_status
from surfaces.shared.terminal.components.subcommand_menu import repl_choose_subcommand


def prompt_login_action() -> str | None:
    """Choose a connection operation without reading secrets or changing state."""
    return repl_choose_subcommand(parent="/login", options=CLI_COMMAND_CHOICES[("/login",)])


def prompt_connection_target(action: str) -> str | None:
    """Select a saved connection for verification or provider-owned disconnection."""
    options = tuple(
        (profile.name, profile.label)
        for profile in iter_auth_profiles()
        if (status := provider_status(profile.name)).authenticated or status.stale
    )
    if not options:
        from rich.console import Console

        Console().print("No model connections found. Connect a provider with /login connect.")
        return None
    return repl_choose_subcommand(parent=f"/login {action}", options=options)
