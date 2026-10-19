"""Workspace knowledge: the organization-wide store beside each member's memory.

It is the memory engine pointed at another root (``<org root>/knowledge/``,
see :func:`config.constants.get_knowledge_dir`): the same file format, safety
checks, ranking, usage counts, locks and caches. Only the entry types differ.
A workspace entry is never about one person, so ``user`` and ``preference``
are refused here, and the scan-only ``organization`` and ``integration`` types
are accepted only here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from config.constants import get_knowledge_dir
from core.domain.memory import (
    MemoryRecord,
    MemorySource,
    MemoryType,
    delete_memory,
    ensure_memory_store,
    live_memories,
    load_live_memory,
    memory_root,
    save_memory,
)
from core.domain.memory.models import PERSONAL_MEMORY_TYPES

#: Every type a workspace entry may carry.
WORKSPACE_TYPES: frozenset[MemoryType] = frozenset(MemoryType) - PERSONAL_MEMORY_TYPES

#: The one entry every member's prompt carries in full.
ORGANIZATION_SLUG = "organization"


def integration_slug(service: str) -> str:
    """The entry name for one connected integration, e.g. ``integration-slack``."""
    return f"integration-{service.strip().lower()}"


def knowledge_dir() -> Path:
    """Where this organization's workspace knowledge lives."""
    return get_knowledge_dir()


@contextmanager
def workspace_store() -> Iterator[Path]:
    """Bind the memory engine to the workspace store for the block, creating it if needed."""
    with memory_root(knowledge_dir()) as directory:
        ensure_memory_store()
        yield directory


def list_workspace_entries() -> list[MemoryRecord]:
    """Live workspace entries, most recently updated first."""
    if not knowledge_dir().is_dir():
        return []
    with workspace_store():
        return live_memories()


def load_workspace_entry(slug: str) -> MemoryRecord | None:
    """One live workspace entry by name, or ``None``."""
    if not knowledge_dir().is_dir():
        return None
    with workspace_store():
        return load_live_memory(slug)


def save_workspace_entry(
    *,
    slug: str,
    memory_type: MemoryType,
    description: str,
    body: str,
    source: MemorySource = MemorySource.TOOL,
    evidence: str = "",
    verified: bool | None = True,
) -> MemoryRecord | None:
    """Create or replace one workspace entry; ``None`` on I/O failure.

    Raises ``ValueError`` for a personal type, an invalid name or content the
    memory safety checks reject (a secret, a credential).
    """
    if memory_type not in WORKSPACE_TYPES:
        raise ValueError(f"{memory_type} is personal and never stored as workspace knowledge")
    with workspace_store():
        saved = save_memory(
            slug=slug,
            memory_type=memory_type,
            description=description,
            body=body,
            source=source,
            evidence=evidence,
            verified=verified,
        )
    return saved[0] if saved is not None else None


def delete_workspace_entry(slug: str) -> bool:
    """Delete one workspace entry; ``True`` when a file was removed."""
    if not knowledge_dir().is_dir():
        return False
    with workspace_store():
        return delete_memory(slug)


def entry_age_seconds(record: MemoryRecord, *, now: datetime | None = None) -> float | None:
    """Seconds since ``record`` was last written; ``None`` when its stamp is unreadable."""
    try:
        updated = datetime.fromisoformat(record.updated_at)
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - updated).total_seconds()


__all__ = [
    "ORGANIZATION_SLUG",
    "WORKSPACE_TYPES",
    "delete_workspace_entry",
    "entry_age_seconds",
    "integration_slug",
    "knowledge_dir",
    "list_workspace_entries",
    "load_workspace_entry",
    "save_workspace_entry",
    "workspace_store",
]
