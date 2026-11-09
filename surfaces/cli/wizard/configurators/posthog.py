"""Configurator handlers for the PostHog and PostHog MCP integrations."""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY
from integrations.posthog.setup import POSTHOG_SETUP
from integrations.posthog_mcp.setup import POSTHOG_MCP_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_posthog() -> tuple[str, str]:
    return configure_from_spec(
        POSTHOG_SETUP,
        title="PostHog",
        intro=(
            f"[{SECONDARY}]Create a personal API key (phx_...) with read access — "
            "https://posthog.com/docs/api/personal-api-keys[/]"
        ),
    )


def _configure_posthog_mcp() -> tuple[str, str]:
    return configure_from_spec(POSTHOG_MCP_SETUP, title="PostHog MCP")
