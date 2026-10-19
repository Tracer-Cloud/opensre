"""Workspace knowledge in ``memory_recall`` results.

Read-only: a turn never writes workspace knowledge through the memory tools.
Every helper returns nothing when this turn may not read the workspace tier.
"""

from __future__ import annotations

from typing import Any

from core.domain.knowledge import knowledge_available_here, knowledge_dir, workspace_store
from core.domain.memory import (
    MemoryRecord,
    live_memories,
    load_live_memory,
    record_memory_usage,
    search_memories_scored,
)
from tools.system.agent_memory.results import index_result, recall_result


def _readable() -> bool:
    return knowledge_available_here() and knowledge_dir().is_dir()


def _entries(records: list[MemoryRecord], scores: dict[str, float] | None = None) -> list[Any]:
    entries: list[Any] = recall_result(records, total_stored=len(records), scores=scores)[
        "memories"
    ]
    record_memory_usage(entry["name"] for entry in entries)
    for entry in entries:
        entry["scope"] = "workspace"
    return entries


def recall_workspace_entry(slug: str) -> dict[str, Any] | None:
    """One workspace entry in full, or ``None``."""
    if not _readable():
        return None
    with workspace_store():
        record = load_live_memory(slug)
        return _entries([record])[0] if record is not None else None


def search_workspace(query: str, *, limit: int) -> list[Any]:
    """Workspace entries matching ``query``, best first."""
    if not _readable():
        return []
    with workspace_store():
        scored = search_memories_scored(query, limit=limit)
        return _entries(
            [record for record, _score in scored],
            scores={record.slug: score for record, score in scored},
        )


def workspace_index() -> list[Any]:
    """One line per workspace entry."""
    if not _readable():
        return []
    with workspace_store():
        entries: list[Any] = index_result(live_memories())["memories"]
    for entry in entries:
        entry["scope"] = "workspace"
    return entries


def with_workspace(result: dict[str, Any], workspace: list[Any]) -> dict[str, Any]:
    """``result`` with a ``workspace`` list when there is anything in it."""
    if workspace:
        result["workspace"] = workspace
    return result


__all__ = ["recall_workspace_entry", "search_workspace", "with_workspace", "workspace_index"]
