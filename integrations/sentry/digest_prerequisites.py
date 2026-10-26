"""Shared prerequisites for Sentry morning digest automation."""

from __future__ import annotations

from rich.console import Console

from infrastructure.harness_providers import configured_integration_services
from infrastructure.scheduling.scheduler.delivery import require_delivery_provider

_console = Console()


def require_sentry_integration() -> None:
    """Exit when Sentry is not configured."""
    if "sentry" in configured_integration_services():
        return
    _console.print(
        "[red]Sentry is not configured.[/red] Run `opensre integrations setup` and verify "
        "with `opensre integrations verify sentry` before scheduling a digest."
    )
    raise SystemExit(1)


# Stable alias — delivery gate lives in infrastructure.scheduling.scheduler.delivery.
require_digest_delivery_provider = require_delivery_provider


__all__ = ["require_digest_delivery_provider", "require_sentry_integration"]
