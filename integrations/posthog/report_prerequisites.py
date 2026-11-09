"""Shared prerequisites for PostHog metric report automation."""

from __future__ import annotations

from rich.console import Console

from infrastructure.harness_providers import configured_integration_services
from infrastructure.scheduling.scheduler.delivery import require_delivery_provider

_console = Console()

POSTHOG_SERVICE_NAMES: tuple[str, ...] = ("posthog_mcp",)

DEFAULT_POSTHOG_PERIOD = "7d"

_NOT_CONFIGURED_HINT = (
    "PostHog MCP is not configured. Run `opensre integrations setup` and verify "
    "with `opensre integrations verify posthog_mcp` before requesting a report."
)


def posthog_report_available() -> bool:
    """Return True when PostHog MCP (``posthog_mcp``) is configured.

    The REST-only ``posthog`` integration is not enough — the skill talks to
    PostHog through MCP tools (``list_posthog_tools`` / ``call_posthog_tool``).
    """
    configured = configured_integration_services()
    return any(name in configured for name in POSTHOG_SERVICE_NAMES)


def posthog_not_configured_hint() -> str:
    """Human-readable guidance when PostHog MCP is missing."""
    return _NOT_CONFIGURED_HINT


def require_posthog_integration() -> None:
    """Exit when PostHog MCP is not configured."""
    if posthog_report_available():
        return
    _console.print(f"[red]{_NOT_CONFIGURED_HINT}[/red]")
    raise SystemExit(1)


# Stable alias — delivery gate lives in infrastructure.scheduling.scheduler.delivery.
require_report_delivery_provider = require_delivery_provider


__all__ = [
    "DEFAULT_POSTHOG_PERIOD",
    "POSTHOG_SERVICE_NAMES",
    "posthog_not_configured_hint",
    "posthog_report_available",
    "require_posthog_integration",
    "require_report_delivery_provider",
]
