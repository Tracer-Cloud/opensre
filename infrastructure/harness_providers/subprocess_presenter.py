"""Subprocess presenter the default agent gives shell tools.

Streaming shell/CLI output needs a presenter that lives in ``tools`` (its
process helpers) and is therefore invisible to ``core.agent_harness``.
Installing it here lets the default agent execute shell tools instead of
refusing them, without core importing tools or gateway.

The presenter is an immutable value installed once at the composition root
(``bootstrap.adapters``) and read through :func:`resolve_subprocess_presenter`,
mirroring
:class:`infrastructure.scheduling.scheduler.delivery_bundle.ScheduledDeliveryAdapters`.
There is no setter — nothing rewrites it after boot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent_harness.ports import SubprocessPresenterFactory


@dataclass(frozen=True)
class SubprocessPresenterProvider:
    """The presenter factory the default agent gives shell tools, installed once."""

    factory: SubprocessPresenterFactory

    def install(self) -> None:
        """Bind this provider as the process-wide subprocess presenter."""
        global _installed
        _installed = self


_installed: SubprocessPresenterProvider | None = None


def resolve_subprocess_presenter() -> SubprocessPresenterFactory | None:
    """Return the installed presenter factory, or ``None`` before boot."""
    return _installed.factory if _installed is not None else None


def reset() -> None:
    """Clear the installed presenter (tests)."""
    global _installed
    _installed = None


__all__ = ["SubprocessPresenterProvider", "resolve_subprocess_presenter"]
