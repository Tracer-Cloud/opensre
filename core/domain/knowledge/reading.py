"""Workspace knowledge as a turn may read it: members only, or nothing.

The one place a turn-facing surface (``memory_recall``, ``/knowledge``,
``opensre knowledge``) reads the workspace tier. Each read checks
:func:`~core.domain.knowledge.settings.knowledge_available_here` first, so a
caller cannot forget the membership rule; the prompt blocks apply the same
check in :mod:`core.domain.knowledge.render`.
"""

from __future__ import annotations

from core.domain.knowledge.settings import knowledge_available_here
from core.domain.knowledge.store import (
    list_workspace_entries,
    load_workspace_entry,
    workspace_store,
)
from core.domain.memory import MemoryRecord, search_memories_scored


def readable_workspace_entries() -> list[MemoryRecord]:
    """Live workspace entries this caller may read; empty when they may not."""
    return list_workspace_entries() if knowledge_available_here() else []


def readable_workspace_entry(slug: str) -> MemoryRecord | None:
    """One workspace entry this caller may read, or ``None``."""
    return load_workspace_entry(slug) if knowledge_available_here() else None


def search_readable_workspace(query: str, *, limit: int) -> list[tuple[MemoryRecord, float]]:
    """Workspace entries matching ``query``, best first, with scores; empty when not readable."""
    if not knowledge_available_here():
        return []
    with workspace_store():
        return search_memories_scored(query, limit=limit)


__all__ = ["readable_workspace_entries", "readable_workspace_entry", "search_readable_workspace"]
