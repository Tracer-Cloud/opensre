"""The per-provider scheduled-delivery adapters, assembled once at boot.

The executor resolves a ``Provider`` to its delivery adapter through this bundle
instead of importing vendor modules, so a new delivery vendor is added by
extending the bundle at the composition root (``bootstrap.adapters``) — the
scheduler stays vendor-agnostic and imports no ``integrations`` package.

The bundle is an immutable value assembled at a composition root and installed
once (mirrors :class:`infrastructure.scheduling.scheduler.runners.SchedulerRunners`),
so there is no mutable per-provider registry to read-modify-write.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask


@runtime_checkable
class ScheduledDeliveryAdapter(Protocol):
    """Delivers one built message to a provider for a scheduled task."""

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        """Deliver ``message`` for ``task``; return ``(success, error, message_id)``."""


@dataclass(frozen=True)
class ScheduledDeliveryAdapters:
    """Immutable provider -> adapter mapping, installed once at boot."""

    adapters: Mapping[Provider, ScheduledDeliveryAdapter]

    def get(self, provider: Provider) -> ScheduledDeliveryAdapter | None:
        """Return the adapter for ``provider``, or ``None`` if none is bundled."""
        return self.adapters.get(provider)

    def providers(self) -> frozenset[Provider]:
        """Return the providers this bundle can deliver to."""
        return frozenset(self.adapters)

    def install(self) -> None:
        """Bind this bundle as the process-wide delivery adapters."""
        global _installed
        _installed = self


_installed: ScheduledDeliveryAdapters | None = None


def resolve_delivery_adapter(provider: Provider) -> ScheduledDeliveryAdapter | None:
    """Return the installed adapter for ``provider``, or ``None`` if unbundled."""
    return _installed.get(provider) if _installed is not None else None


def installed_delivery_providers() -> frozenset[Provider]:
    """Return the providers the installed bundle can deliver to (empty if none)."""
    return _installed.providers() if _installed is not None else frozenset()


__all__ = [
    "ScheduledDeliveryAdapter",
    "ScheduledDeliveryAdapters",
    "installed_delivery_providers",
    "resolve_delivery_adapter",
]
