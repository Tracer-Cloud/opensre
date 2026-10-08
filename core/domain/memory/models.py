"""Typed contracts for the long-term memory store."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MemoryType(StrEnum):
    """Closed vocabulary for a persisted memory's frontmatter ``type``."""

    USER = "user"
    INFRASTRUCTURE = "infrastructure"
    REPOSITORY = "repository"
    PREFERENCE = "preference"
    INVESTIGATION_LEARNING = "investigation_learning"


class MemorySource(StrEnum):
    """Where a memory's fact came from (frontmatter ``source``)."""

    USER = "user"
    TOOL = "tool"
    ASSISTANT = "assistant"


MEMORY_TYPES: tuple[str, ...] = tuple(t.value for t in MemoryType)
MEMORY_SOURCES: tuple[str, ...] = tuple(s.value for s in MemorySource)

#: Types that describe the person rather than their systems; listed first and
#: never archived for age or fenced as demo output.
PERSONAL_MEMORY_TYPES: frozenset[MemoryType] = frozenset({MemoryType.USER, MemoryType.PREFERENCE})

MAX_DESCRIPTION_CHARS = 200
MAX_BODY_CHARS = 10_000
MAX_SLUG_CHARS = 64
MAX_EVIDENCE_CHARS = 200

TRUNCATION_MARKER = "\n...[truncated]"


@dataclass(frozen=True)
class MemoryRecord:
    """One persisted long-term memory (a single markdown file on disk).

    ``source``, ``evidence`` and ``verified`` are optional provenance; files
    written before provenance existed parse with all three unset.
    """

    slug: str
    memory_type: MemoryType
    description: str
    created_at: str
    updated_at: str
    body: str
    source: MemorySource | None = None
    evidence: str = ""
    verified: bool | None = None

    def __post_init__(self) -> None:
        # Coerce plain strings (from disk / tool args) into real enum members so
        # ``memory_type`` is always a ``MemoryType`` regardless of entry point.
        object.__setattr__(self, "memory_type", MemoryType(self.memory_type))
        if self.source is not None:
            object.__setattr__(self, "source", MemorySource(self.source))


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MAX_SLUG_CHARS",
    "MEMORY_SOURCES",
    "MEMORY_TYPES",
    "PERSONAL_MEMORY_TYPES",
    "TRUNCATION_MARKER",
    "MemoryRecord",
    "MemorySource",
    "MemoryType",
]
