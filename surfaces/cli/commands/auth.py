"""CLI commands for LLM provider authentication."""

from __future__ import annotations

import sys
import webbrowser
from collections.abc import Iterable

import click
import questionary
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from infrastructure.terminal.prompt_support import (
    QUESTIONARY_QMARK,
    questionary_prompt_style,
)
from infrastructure.terminal.theme import BOLD_BRAND, ERROR, HIGHLIGHT, SECONDARY, WARNING
from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from surfaces.shared.llm_setup.auth_service import (
    AuthSetupError,
    configure_api_key_provider,
    logout_provider,
    provider_status,
    verify_provider,
)


def _provider_choices() -> str:
    return ", ".join(profile.name for profile in iter_auth_profiles())


def _resolve_or_raise(provider: str) -> ProviderAuthProfile:
    try:
        return resolve_auth_profile(provider)
    except KeyError as exc:
        raise click.UsageError(
            f"Unknown auth provider '{provider}'. Choose one of: {_provider_choices()}."
        ) from exc


def _provider_choice(profile: ProviderAuthProfile) -> questionary.Choice:
    return questionary.Choice(f"{profile.name} ({profile.label})", value=profile.name)


def _configured_profile_name() -> str | None:
    """The auth-profile name matching the install's active LLM provider, if any."""
    from config.llm_settings import get_configured_llm_provider

    configured = get_configured_llm_provider()
    for profile in iter_auth_profiles():
        if configured == profile.provider_value or configured in profile.all_names:
            return profile.name
    return None


def _prompt_provider() -> ProviderAuthProfile:
    choices: list[questionary.Choice | questionary.Separator] = [
        questionary.Separator(" "),
        questionary.Separator("API-key providers"),
    ]
    choices.extend(_provider_choice(profile) for profile in iter_auth_profiles())

    configured = _configured_profile_name()
    default = next(
        (
            choice
            for choice in choices
            if isinstance(choice, questionary.Choice) and choice.value == configured
        ),
        None,
    )
    provider = questionary.select(
        "Choose a provider:",
        choices=choices,
        default=default,
        qmark=QUESTIONARY_QMARK,
        style=questionary_prompt_style(),
        instruction="(use arrow keys)",
    ).ask()
    if provider is None:
        raise click.Abort()
    return _resolve_or_raise(provider)


def _maybe_open_setup_page(profile: ProviderAuthProfile, *, enabled: bool) -> None:
    if not enabled or not profile.setup_url:
        return
    if not sys.stdin.isatty():
        click.echo(f"Setup page: {profile.setup_url}")
        return
    if click.confirm(f"Open {profile.label} setup page in your browser?", default=True):
        opened = False
        try:
            opened = bool(webbrowser.open(profile.setup_url))
        finally:
            from infrastructure.analytics.capture import capture_browser_open_requested

            capture_browser_open_requested(target="provider_setup", opened=opened)


# Mirrors render_health_report's table style (surfaces/shared/terminal/health) so
# /auth and /health read consistently. Real ANSI colour is intentional: run_cli_command
# captures this command's stdout with FORCE_COLOR=1 and Text.from_ansi() re-parses it
# for the REPL, so force_terminal=True here is what makes that styling survive.
_console = Console(
    highlight=False, force_terminal=True, color_system="truecolor", legacy_windows=False
)


def _status_badge(state: str) -> Text:
    if state == "ok":
        return Text("ok", style=f"bold {HIGHLIGHT}")
    if state == "stale":
        return Text("stale", style=f"bold {WARNING}")
    return Text("missing", style=f"bold {ERROR}")


def _render_status_table(providers: Iterable[ProviderAuthProfile]) -> None:
    table = Table(title="Auth status", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Provider", style=BOLD_BRAND)
    table.add_column("Status")
    table.add_column("Source", style=SECONDARY)
    table.add_column("Detail")
    for profile in providers:
        status = provider_status(profile.name)
        state = "stale" if status.stale else "ok" if status.authenticated else "missing"
        table.add_row(profile.name, _status_badge(state), status.source, status.detail)
    _console.print(table)


@click.group(name="auth", invoke_without_command=True)
@click.pass_context
def auth_command(ctx: click.Context) -> None:
    """Log in to LLM providers and inspect local auth state."""
    if ctx.invoked_subcommand is None:
        _render_status_table(iter_auth_profiles())


@auth_command.command(name="login")
@click.argument("provider", required=False)
@click.option("--api-key", help="API key to store for API-key providers.")
@click.option("--model", help="Reasoning model to persist with the provider selection.")
@click.option(
    "--set-provider/--no-set-provider",
    default=True,
    show_default=True,
    help="Persist LLM_PROVIDER and provider model settings after login.",
)
@click.option(
    "--validate/--no-validate",
    default=True,
    show_default=True,
    help="Run a tiny live validation request before storing API keys.",
)
@click.option(
    "--open-browser/--no-open-browser",
    default=True,
    show_default=True,
    help="Offer to open the provider setup page for API-key flows.",
)
def auth_login(
    provider: str | None,
    api_key: str | None,
    model: str | None,
    set_provider: bool,
    validate: bool,
    open_browser: bool,
) -> None:
    """Configure one LLM provider auth path."""
    profile = _resolve_or_raise(provider) if provider else _prompt_provider()
    try:
        _maybe_open_setup_page(profile, enabled=open_browser)
        resolved_key = api_key
        if resolved_key is None:
            resolved_key = click.prompt(
                f"{profile.label} key",
                hide_input=True,
                confirmation_prompt=False,
                type=str,
            )
        result = configure_api_key_provider(
            profile=profile,
            api_key=resolved_key,
            model=model,
            set_provider=set_provider,
            validate=validate,
        )
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Authenticated: {profile.label}")
    click.echo(f"Provider     : {result.provider}")
    click.echo(f"Credential   : {result.source}")
    if result.model:
        click.echo(f"Model        : {result.model}")
    if result.env_path:
        click.echo(f"Env          : {result.env_path}")
    click.echo(f"Status       : {result.detail}")
    click.echo(f"Verify       : opensre auth verify {result.provider}")


@auth_command.command(name="status")
@click.argument("provider", required=False)
def auth_status(provider: str | None) -> None:
    """Show provider auth status."""
    profiles = (_resolve_or_raise(provider),) if provider else iter_auth_profiles()
    _render_status_table(profiles)


@auth_command.command(name="verify")
@click.argument("provider")
def auth_verify(provider: str) -> None:
    """Intentionally verify one provider's request-time credentials."""
    _resolve_or_raise(provider)
    try:
        status = verify_provider(provider)
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc
    state = "ok" if status.authenticated else "missing"
    if status.stale:
        state = "stale"
    click.echo(f"Provider : {status.provider}")
    click.echo(f"Status   : {state}")
    click.echo(f"Source   : {status.source}")
    click.echo(f"Detail   : {status.detail}")


@auth_command.command(name="logout")
@click.argument("provider")
def auth_logout(provider: str) -> None:
    """Clear OpenSRE-managed auth for a provider."""
    _resolve_or_raise(provider)
    try:
        detail = logout_provider(provider)
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(detail)


__all__ = ["auth_command"]
