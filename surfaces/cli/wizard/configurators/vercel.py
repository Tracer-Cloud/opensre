"""Configurator handler for the Vercel integration."""

from __future__ import annotations

from integrations.vercel import VERCEL_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_vercel() -> tuple[str, str]:
    return configure_from_spec(VERCEL_SETUP, title="Vercel")
