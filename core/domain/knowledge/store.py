"""Workspace knowledge on disk: the organization-wide store beside each member's memory.

It is the memory engine pointed at another root (``<org root>/knowledge/``,
see :func:`config.constants.get_knowledge_dir`): the same file format, safety
checks, ranking, locks and caches. Only the entry types differ. A workspace
entry is never about one person, so ``user`` and ``preference`` are refused
here, and the scan-only ``organization`` and ``integration`` types are
accepted only here.

These functions do not check who is asking: they serve the scans that write
the store and the web app mirror. A turn reads it through
:mod:`core.domain.knowledge.reading`, which admits only members.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
    """Bind the memory engine to the workspace store for the block; reads only.

    Binding never creates anything: the engine reads a missing store as empty.
    """
    with memory_root(knowledge_dir()) as directory:
        yield directory


@contextmanager
def writable_workspace_store() -> Iterator[Path]:
    """Bind the workspace store for a write, creating it (and its index) if needed."""
    with workspace_store() as directory:
        ensure_memory_store()
        yield directory


def list_workspace_entries() -> list[MemoryRecord]:
    """Live workspace entries, most recently updated first."""
    with workspace_store():
        return live_memories()


def load_workspace_entry(slug: str) -> MemoryRecord | None:
    """One live workspace entry by name, or ``None``."""
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
    with writable_workspace_store():
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
    if not workspace_entry_exists(slug):
        return False
    with writable_workspace_store():
        return delete_memory(slug)


def workspace_entry_exists(slug: str) -> bool:
    """Whether a workspace entry's file is on disk (live or not)."""
    return (knowledge_dir() / f"{slug}.md").is_file()


__all__ = [
    "ORGANIZATION_SLUG",
    "WORKSPACE_TYPES",
    "delete_workspace_entry",
    "integration_slug",
    "knowledge_dir",
    "list_workspace_entries",
    "load_workspace_entry",
    "save_workspace_entry",
    "workspace_entry_exists",
    "workspace_store",
    "writable_workspace_store",
]
