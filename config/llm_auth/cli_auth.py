"""Port for checking a local CLI provider's auth, filled once at boot.

``config.llm_auth.credentials`` reports prompt-safe auth status without importing
``integrations``; the integrations-backed checker is bundled at the composition
root (``bootstrap.adapters``) and installed once, mirroring
:class:`infrastructure.scheduling.scheduler.delivery_bundle.ScheduledDeliveryAdapters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CliAuthState:
    """Whether one CLI provider is installed and logged in locally."""

    installed: bool
    logged_in: bool | None
    detail: str


@runtime_checkable
class CliAuthCheck(Protocol):
    """Checks a CLI provider's local install and login."""

    def __call__(self, provider: str) -> CliAuthState | None:
        """Return the provider's auth state, or ``None`` if it has no CLI adapter."""


@dataclass(frozen=True)
class CliAuthChecker:
    """Immutable CLI-auth checker, installed once at boot."""

    check: CliAuthCheck

    def install(self) -> None:
        """Bind this checker as the process-wide CLI-auth checker."""
        global _installed
        _installed = self


_installed: CliAuthChecker | None = None


def resolve_cli_auth_state(provider: str) -> CliAuthState | None:
    """Return the installed checker's result for ``provider``.

    ``None`` when no checker is installed or the provider has no CLI adapter.
    """
    return _installed.check(provider) if _installed is not None else None


__all__ = ["CliAuthCheck", "CliAuthChecker", "CliAuthState", "resolve_cli_auth_state"]
