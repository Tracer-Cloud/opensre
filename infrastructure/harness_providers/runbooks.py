"""Registry for provider-owned runbook source implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import RunbookSource

RunbookSourceFactory = Callable[
    [RunbookSourceConfig, dict[str, Any]],
    RunbookSource | None,
]

_providers: dict[str, RunbookSourceFactory] = {}


def register_runbook_source_provider(provider: str, factory: RunbookSourceFactory) -> None:
    """Register the factory for one provider identifier."""
    _providers[provider.strip().lower()] = factory


def clear_runbook_source_providers() -> None:
    """Clear registered providers for deterministic process boot and tests."""
    _providers.clear()


def resolve_runbook_source(
    config: RunbookSourceConfig,
    resolved_integrations: dict[str, Any],
) -> RunbookSource | None:
    """Build a configured source when its provider is installed and available."""
    factory = _providers.get(config.provider)
    if factory is None:
        return None
    return factory(config, resolved_integrations)


def reset() -> None:
    """Restore the empty provider registry."""
    clear_runbook_source_providers()


__all__ = [
    "RunbookSourceFactory",
    "clear_runbook_source_providers",
    "register_runbook_source_provider",
    "resolve_runbook_source",
]
