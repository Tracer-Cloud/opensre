"""CLI commands for a personal OpenSRE account."""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from config.constants.account import OPENSRE_APP_URL_DEV
from surfaces.cli import account_auth
from surfaces.cli.account_ui import (
    AccountLoginPresenter,
    render_account_logout,
    render_account_status,
)
from surfaces.shared.account_session import AccountSessionState, AccountStatus, account_status


def _json_enabled(ctx: click.Context) -> bool:
    return bool(ctx.find_root().obj.get("json", False))


def _dev_enabled(ctx: click.Context, dev: bool) -> bool:
    return bool(dev or ctx.find_root().obj.get("account_dev", False))


def _optional_app_url(*, app_url: str | None, dev: bool) -> str | None:
    if app_url:
        return app_url
    if dev:
        return OPENSRE_APP_URL_DEV
    return None


def _render_status(status: AccountStatus, *, json_output: bool) -> None:
    if json_output:
        click.echo(
            json.dumps(
                {
                    "state": status.state.value,
                    "authenticated": status.authenticated,
                    "detail": status.detail,
                    "account": asdict(status.record) if status.record else None,
                },
                indent=2,
            )
        )
        return
    render_account_status(status)


def _already_active_json(status: AccountStatus) -> str:
    record = status.record
    return json.dumps(
        {
            "state": status.state.value,
            "authenticated": True,
            "already_active": True,
            "account": asdict(record) if record else None,
            "warning": None,
            "detail": "A valid OpenSRE session is already active.",
        },
        indent=2,
    )


@click.group(name="account", invoke_without_command=True)
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.pass_context
def account_command(ctx: click.Context, dev: bool) -> None:
    """Sign in to OpenSRE and inspect the local account."""
    ctx.ensure_object(dict)
    ctx.find_root().obj["account_dev"] = dev
    if ctx.invoked_subcommand is None:
        _render_status(
            account_status(app_url=_optional_app_url(app_url=None, dev=dev)),
            json_output=_json_enabled(ctx),
        )


@account_command.command(name="login")
@click.option(
    "--app-url",
    default=None,
    metavar="URL",
    help="OpenSRE webapp origin (or set OPENSRE_APP_URL).",
)
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.option(
    "--browser/--no-browser",
    default=True,
    show_default=True,
    help="Open the OpenSRE sign-in page automatically.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    default=300.0,
    show_default=True,
    type=click.FloatRange(min=1.0, max=1800.0),
    help="Seconds to wait for the browser callback.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Replace a valid existing session without prompting.",
)
@click.pass_context
def account_login(
    ctx: click.Context,
    app_url: str | None,
    dev: bool,
    browser: bool,
    timeout_seconds: float,
    force: bool,
) -> None:
    """Sign in or create a personal OpenSRE account."""
    json_output = _json_enabled(ctx)
    presenter = AccountLoginPresenter()
    resolved_app_url = _optional_app_url(app_url=app_url, dev=_dev_enabled(ctx, dev))
    status = account_status(app_url=resolved_app_url)
    if status.authenticated and not force:
        if json_output:
            click.echo(_already_active_json(status))
            return
        presenter.warn_active_session(status)
        if not presenter.confirm_replace():
            presenter.session_kept()
            return
    elif status.authenticated and force and not json_output:
        presenter.replacing_session(status)

    try:
        result = account_auth.login_account(
            app_url=resolved_app_url,
            open_browser=browser,
            timeout_seconds=timeout_seconds,
            progress=None if json_output else presenter,
        )
    except account_auth.AccountAuthError as exc:
        raise click.ClickException(str(exc)) from exc

    record = result.record
    if json_output:
        click.echo(
            json.dumps(
                {
                    "state": AccountSessionState.ACTIVE.value,
                    "authenticated": True,
                    "account": asdict(record),
                    "warning": result.warning or None,
                },
                indent=2,
            )
        )
        return

    presenter.success(result)


@account_command.command(name="status")
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.pass_context
def account_status_command(ctx: click.Context, dev: bool) -> None:
    """Validate and display the current personal account."""
    _render_status(
        account_status(app_url=_optional_app_url(app_url=None, dev=_dev_enabled(ctx, dev))),
        json_output=_json_enabled(ctx),
    )


@account_command.command(name="logout")
@click.pass_context
def account_logout(ctx: click.Context) -> None:
    """Revoke the OpenSRE token and clear local account credentials."""
    try:
        result = account_auth.logout_account()
    except account_auth.AccountAuthError as exc:
        raise click.ClickException(str(exc)) from exc
    if _json_enabled(ctx):
        click.echo(
            json.dumps(
                {
                    "signed_out": True,
                    "remote_revoked": result.remote_revoked,
                    "detail": result.detail,
                },
                indent=2,
            )
        )
        return
    render_account_logout(result)


__all__ = ["account_command"]
