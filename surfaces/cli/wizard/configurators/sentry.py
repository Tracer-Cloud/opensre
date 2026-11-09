"""Configurator handlers for the Sentry and Sentry MCP integrations."""

from __future__ import annotations

from infrastructure.terminal.theme import SECONDARY
from integrations.sentry import get_sentry_auth_recommendations
from integrations.sentry.setup import SENTRY_SETUP
from integrations.sentry_mcp.setup import SENTRY_MCP_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_sentry_mcp() -> tuple[str, str]:
    return configure_from_spec(SENTRY_MCP_SETUP, title="Sentry MCP")


def _configure_sentry() -> tuple[str, str]:
    guidance = get_sentry_auth_recommendations()
    return configure_from_spec(
        SENTRY_SETUP,
        title="Sentry",
        intro=(
            f"[{SECONDARY}]Recommended: "
            f"{guidance['recommended_token_type']} from {guidance['where_to_create']}. "
            f"{guidance['fallback_token_type']} only if you need broader scopes.[/]"
        ),
    )
