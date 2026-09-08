"""First-run factory setup CLI command."""

from __future__ import annotations

from functools import partial

import click

from config.constants.account import OPENSRE_APP_URL_DEV


@click.command(name="setup")
@click.option(
    "--dev",
    is_flag=True,
    help="Use the local webapp at http://localhost:3000.",
)
@click.pass_context
def setup_command(ctx: click.Context, dev: bool) -> None:
    """Sign in to OpenSRE, activate the hosted LLM, then open the shell."""
    from surfaces.cli.commands.onboard import _run_onboarding_command
    from surfaces.cli.wizard.factory_setup import run_factory_setup

    app_url = OPENSRE_APP_URL_DEV if dev else None
    _run_onboarding_command(partial(run_factory_setup, app_url=app_url), ctx=ctx)
