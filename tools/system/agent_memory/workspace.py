"""Workspace knowledge in ``memory_recall`` results.

Read-only, and only what this turn may read
(:mod:`core.domain.knowledge.reading`): a turn never writes workspace
knowledge through the memory tools, and a non-member gets nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.domain.knowledge import (
    readable_workspace_entries,
    readable_workspace_entry,
    search_readable_workspace,
)
from core.domain.memory import MemoryRecord
from tools.system.agent_memory.results import index_result, recall_result


def _labelled(entries: list[Any]) -> list[Any]:
    for entry in entries:
        entry["scope"] = "workspace"
    return entries


def _entries(records: Sequence[MemoryRecord], scores: dict[str, float] | None = None) -> list[Any]:
    result = recall_result(records, total_stored=len(records), scores=scores)
    return _labelled(result["memories"])


def recall_workspace_entry(slug: str) -> dict[str, Any] | None:
    """One workspace entry in full, or ``None``."""
    record = readable_workspace_entry(slug)
    return _entries([record])[0] if record is not None else None


def search_workspace(query: str, *, limit: int) -> list[Any]:
    """Workspace entries matching ``query``, best first."""
    scored = search_readable_workspace(query, limit=limit)
    return _entries(
        [record for record, _score in scored],
        scores={record.slug: score for record, score in scored},
    )


def workspace_index() -> list[Any]:
    """One line per workspace entry."""
    return _labelled(index_result(readable_workspace_entries())["memories"])


def with_workspace(result: dict[str, Any], workspace: list[Any]) -> dict[str, Any]:
    """``result`` with a ``workspace`` list when there is anything in it."""
    if workspace:
        result["workspace"] = workspace
    return result


__all__ = ["recall_workspace_entry", "search_workspace", "with_workspace", "workspace_index"]
