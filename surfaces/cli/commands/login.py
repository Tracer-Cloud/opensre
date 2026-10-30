"""Provider connections through login, with the legacy auth command preserved."""

from __future__ import annotations

from collections.abc import Iterable
from copy import copy

import click
from click.core import ParameterSource
from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import BOLD_BRAND, ERROR, HIGHLIGHT, SECONDARY, WARNING
from surfaces.shared.llm_setup.auth_menu import prompt_auth_provider
from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    iter_auth_profiles,
    resolve_auth_profile,
)
from surfaces.shared.llm_setup.auth_service import (
    AuthSetupError,
    logout_provider,
    provider_status,
    verify_provider,
)
from surfaces.shared.llm_setup.connection_summary import summarize_connection
from surfaces.shared.llm_setup.login_menu import prompt_connection_target, prompt_login_action
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def _provider_choices() -> str:
    return ", ".join(profile.name for profile in iter_auth_profiles())


def _resolve_or_raise(provider: str) -> ProviderAuthProfile:
    try:
        return resolve_auth_profile(provider)
    except KeyError as exc:
        raise click.UsageError(
            f"Unknown model provider '{provider}'. Choose one of: {_provider_choices()}."
        ) from exc


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
    import os

    from config.account import load_account_record
    from config.constants.account import OPENSRE_ACCOUNT_TOKEN_ENV
    from config.constants.billing import WEBAPP_URL_ENV
    from config.constants.llm import OPENSRE_LLM_CONNECTION_ENV
    from config.llm_settings import get_configured_llm_provider

    # Prompt-safe selection hints never resolve the account's stored bearer token.
    local = os.getenv(OPENSRE_LLM_CONNECTION_ENV, "").strip().lower() == "local"
    account_hint = load_account_record() is not None or (
        os.getenv(OPENSRE_ACCOUNT_TOKEN_ENV) and os.getenv(WEBAPP_URL_ENV)
    )
    active = (
        "OpenSRE hosted · OpenSRE credits"
        if account_hint and not local
        else get_configured_llm_provider()
    )
    rows = []
    for profile in providers:
        status = provider_status(profile.name)
        summary = summarize_connection(profile)
        state = "stale" if status.stale else "ok" if status.authenticated else "missing"
        rows.append(
            RecordRow(
                cells=(
                    Text(summary.provider, style=BOLD_BRAND),
                    _status_badge(state),
                    Text(summary.route, style=SECONDARY),
                    Text(summary.billing, style=SECONDARY),
                ),
                details=(
                    *(Text(line) for line in summary.detail_lines),
                    Text(f"Credential source: {status.source}"),
                    Text(status.detail),
                ),
            )
        )
    _console.print(
        RecordTable(
            title="Model connections",
            subtitle=f"Active provider: {active}",
            columns=tuple(
                RecordColumn(name) for name in ("Provider", "Status", "Route", "Billing")
            ),
            rows=tuple(rows),
        )
    )


class _LoginGroup(click.Group):
    """Accept the established ``login <provider>`` shorthand as ``login connect``."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ctx.help_option_names:
            args = ["connect", *args]
        return super().parse_args(ctx, args)


@click.group(name="login", cls=_LoginGroup, invoke_without_command=True)
@click.pass_context
def login_command(ctx: click.Context) -> None:
    """Connect and manage external model providers."""
    if ctx.invoked_subcommand is not None:
        return
    if not repl_tty_interactive():
        raise click.UsageError(
            "Supply an action or provider: opensre login list, "
            "opensre login <provider>, or opensre login --help."
        )
    action = prompt_login_action()
    if action is not None:
        ctx.invoke(login_command.commands[action])


@click.group(name="auth", invoke_without_command=True, hidden=True)
@click.pass_context
def auth_command(ctx: click.Context) -> None:
    """Compatibility commands; use opensre login to manage model connections."""
    if ctx.invoked_subcommand is None:
        _render_status_table(iter_auth_profiles())


@login_command.command(name="connect")
@click.argument("provider", required=False)
@click.option("--api-key", help="API key to store for API-key providers.")
@click.option("--model", help="Reasoning model to persist with the provider selection.")
@click.option(
    "--set-provider/--no-set-provider",
    default=None,
    help="Explicitly select the provider after login, or connect only with --no-set-provider.",
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
    help="Open the provider setup page or subscription authorization in your browser.",
)
@click.option("--profile", "profile_id", help="Saved ChatGPT registration ID from login status.")
@click.option("--new-account", is_flag=True, help="Register another ChatGPT account or workspace.")
def connect_command(
    provider: str | None,
    api_key: str | None,
    model: str | None,
    set_provider: bool | None,
    validate: bool,
    open_browser: bool,
    profile_id: str | None,
    new_account: bool,
) -> None:
    """Configure one LLM provider auth path."""
    if provider is None and not repl_tty_interactive():
        raise click.UsageError("Supply a provider: opensre login <provider> --api-key <key>.")
    profile = _resolve_or_raise(provider) if provider else prompt_auth_provider()
    if profile is None:
        raise click.Abort()
    if profile_id and new_account:
        raise click.UsageError("Use --profile or --new-account, not both.")
    if profile.kind != "subscription" and (profile_id or new_account):
        raise click.UsageError("--profile and --new-account apply to subscription login.")
    from surfaces.shared.llm_setup.auth_results import SelectionError
    from surfaces.shared.llm_setup.connection import connect_provider
    from surfaces.shared.llm_setup.model_picker import choose_model
    from surfaces.shared.llm_setup.selection import select_provider

    if api_key is not None and profile.kind == "subscription":
        from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE

        if SUBSCRIPTION_BY_VALUE[profile.provider_value].auth_method != "api_key":
            raise click.UsageError(f"{profile.label} uses oauth authorization, not an API key.")
    interactive = repl_tty_interactive()
    try:
        result = connect_provider(
            _console,
            profile.name,
            api_key=api_key,
            model=model,
            validate=validate,
            open_browser=open_browser
            and (
                interactive
                or click.get_current_context().get_parameter_source("open_browser")
                == ParameterSource.COMMANDLINE
            ),
            profile_id=profile_id,
            new_account=new_account,
        )
        click.echo(f"Connected: {profile.label}")
        if result.detail:
            _console.print(Text(result.detail))
        if set_provider is True and (model is not None or not interactive):
            selected = select_provider(result.provider, model or result.model or None)
            click.echo(f"Now using {selected.model or 'CLI default'} · {selected.provider}")
        elif set_provider is not False and interactive:
            choose_model(_console, provider=result.provider, initial_model=model or result.model)
        else:
            click.echo(f"Choose a model: opensre model set {result.provider} <model>")
    except (AuthSetupError, SelectionError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Verify: opensre login verify {result.provider}")


@login_command.command(name="status")
@click.argument("provider", required=False)
def status_command(provider: str | None) -> None:
    """Show model connections without retrieving secrets or contacting providers."""
    profiles = (_resolve_or_raise(provider),) if provider else iter_auth_profiles()
    _render_status_table(profiles)


@login_command.command(name="verify")
@click.argument("provider", required=False)
def verify_command(provider: str | None) -> None:
    """Intentionally verify one provider's request-time credentials."""
    if provider is None:
        provider = _connection_target("verify")
        if provider is None:
            return
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


@login_command.command(name="disconnect")
@click.argument("provider", required=False)
def disconnect_command(provider: str | None) -> None:
    """Clear OpenSRE-managed auth for a provider."""
    if provider is None:
        provider = _connection_target("disconnect")
        if provider is None:
            return
    _resolve_or_raise(provider)
    try:
        detail = logout_provider(provider)
    except AuthSetupError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(detail)
    from surfaces.shared.llm_setup.handoff import notify_setup

    notify_setup(disconnected=resolve_auth_profile(provider).provider_value)
    click.echo(
        "The active model selection was not changed. "
        "Use opensre model to select another connection if needed."
    )


def _connection_target(action: str) -> str | None:
    if not repl_tty_interactive():
        raise click.UsageError(f"Supply a provider: opensre login {action} <provider>.")
    return prompt_connection_target(action)


login_command.add_command(status_command, name="list")
# Status remains callable without advertising the same action twice in menus.
_status_alias = copy(status_command)
_status_alias.hidden = True
login_command.add_command(_status_alias, name="status")
auth_command.add_command(connect_command, name="login")
auth_command.add_command(status_command, name="status")
auth_command.add_command(verify_command, name="verify")
auth_command.add_command(disconnect_command, name="logout")

__all__ = ["auth_command", "login_command"]
