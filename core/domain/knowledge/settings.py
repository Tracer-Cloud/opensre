"""Environment gates for workspace knowledge."""

from __future__ import annotations

import os

from config.constants import (
    OPENSRE_KNOWLEDGE_DISABLED_ENV,
    OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV,
)
from core.domain.knowledge.access import caller_may_read_workspace
from core.domain.memory import memory_available_here, memory_enabled

_TRUTHY = frozenset({"1", "true", "yes"})
_FALSY = frozenset({"0", "false", "no"})


def knowledge_enabled() -> bool:
    """Whether workspace knowledge exists at all here (scans, sync, CLI)."""
    return (
        memory_enabled()
        and os.getenv(OPENSRE_KNOWLEDGE_DISABLED_ENV, "").strip().lower() not in _TRUTHY
    )


def knowledge_available_here() -> bool:
    """Whether this turn's prompt and tools may read workspace knowledge.

    Follows the memory gate, so a gateway that keeps memory off keeps the
    workspace tier off too, and admits only full members of the organization
    (:mod:`core.domain.knowledge.access`).
    """
    return knowledge_enabled() and memory_available_here() and caller_may_read_workspace()


def scan_messages_enabled() -> bool:
    """Whether scans may read channel messages, not only workspace metadata."""
    return os.getenv(OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV, "").strip().lower() not in _FALSY


__all__ = ["knowledge_available_here", "knowledge_enabled", "scan_messages_enabled"]
