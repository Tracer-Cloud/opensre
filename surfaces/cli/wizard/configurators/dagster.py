"""Configurator handler for the Dagster integration."""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY
from integrations.dagster.setup import DAGSTER_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec

_INTRO = (
    "\n[bold]Dagster Integration[/bold]\n"
    f"[{SECONDARY}]Dagster webserver URL. "
    "OSS local dev: http://localhost:3000. "
    "Dagster+: https://<deployment>.dagster.cloud/<env>. "
    "API token required for Dagster+; leave blank for unauthenticated OSS.[/]\n"
)


def _configure_dagster() -> tuple[str, str]:
    return configure_from_spec(DAGSTER_SETUP, title="Dagster", intro=_INTRO)
