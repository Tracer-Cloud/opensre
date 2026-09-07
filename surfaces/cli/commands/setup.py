"""First-run factory setup CLI command."""

from __future__ import annotations

import click


@click.command(name="setup")
@click.pass_context
def setup_command(ctx: click.Context) -> None:
    """Sign in with GitHub, activate the OpenSRE-hosted LLM, then open the shell."""
    from surfaces.cli.commands.onboard import _run_onboarding_command
    from surfaces.cli.wizard.factory_setup import run_factory_setup

    _run_onboarding_command(run_factory_setup, ctx=ctx)
