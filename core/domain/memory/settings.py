"""Environment gates for the long-term memory feature."""

from __future__ import annotations

import os

from config.constants import (
    OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV,
    OPENSRE_MEMORY_DISABLED_ENV,
    OPENSRE_MEMORY_GATEWAY_ENABLED_ENV,
)
from infrastructure.analytics.usage_context import UsageSurface, get_surface

_TRUTHY = frozenset({"1", "true", "yes"})
_GATEWAY_ANALYTICS_SURFACES = frozenset({UsageSurface.SLACK, UsageSurface.TELEGRAM})


def _flag_set(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def memory_enabled() -> bool:
    """Return whether the long-term memory feature is enabled."""
    return not _flag_set(OPENSRE_MEMORY_DISABLED_ENV)


def gateway_memory_enabled() -> bool:
    """Return whether memory is opted in for shared gateway (Slack/Telegram) hosts."""
    return _flag_set(OPENSRE_MEMORY_GATEWAY_ENABLED_ENV)


def memory_available_here() -> bool:
    """Return whether memory tools/prompt injection may run in the current surface.

    The CLI stays on when memory is enabled. Slack/Telegram gateway
    turns require an explicit ``OPENSRE_MEMORY_GATEWAY_ENABLED`` opt-in: Slack
    memory is now per-user scoped (``<org root>/users/<id>/memory``), but Telegram
    stays host-global, so the whole feature is off by default on shared gateway
    hosts to avoid one user's facts crossing into another's prompts.
    """
    if not memory_enabled():
        return False
    surface = get_surface()
    if surface in _GATEWAY_ANALYTICS_SURFACES:
        return gateway_memory_enabled()
    return True


def auto_extract_enabled() -> bool:
    """Session-end LLM extraction; also off whenever memory itself is disabled."""
    return memory_available_here() and not _flag_set(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV)


__all__ = [
    "auto_extract_enabled",
    "gateway_memory_enabled",
    "memory_available_here",
    "memory_enabled",
]
