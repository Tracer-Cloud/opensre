"""Every member's memory store in this organization, for audits and the web app mirror.

On a hosted gateway each member has ``<org root>/users/<actor id>/memory/``
(:func:`config.constants.paths.member_memory_path`, the layout their own turns
use). Stores are read uncached (:func:`read_memories`): a pass over every
member would otherwise push the stores members' turns are using out of the
engine's small cache. Nothing here is reachable from a turn: a member's turn
only ever resolves its own directory.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.constants import OPENSRE_MEMORY_DIR_ENV
from config.constants.paths import (
    USERS_DIR_NAME,
    UnsafePathSegmentError,
    deployment_home,
    get_memory_dir,
    member_memory_path,
)
from core.domain.memory.fence import is_fenced_record
from core.domain.memory.models import MemoryRecord
from core.domain.memory.stamps import last_activity
from core.domain.memory.store import read_memories
from core.domain.memory.usage import MemoryUsage, load_usage

#: The actor named for a store that is not under ``users/`` (a laptop's own).
LOCAL_ACTOR = "local"


@dataclass(frozen=True)
class MemberStore:
    """One member's live memories and usage."""

    actor: str
    directory: Path
    records: tuple[MemoryRecord, ...]
    usage: dict[str, MemoryUsage]

    def last_active(self) -> datetime | None:
        """When a memory here was last written or used; ``None`` for an empty store."""
        stamps = (
            last_activity(record, self.usage.get(record.slug, MemoryUsage()))
            for record in self.records
        )
        return max((stamp for stamp in stamps if stamp is not None), default=None)


def _member_dir(actor: str) -> Path | None:
    try:
        directory = member_memory_path(actor)
    except UnsafePathSegmentError:
        return None
    return directory if directory.is_dir() else None


def _organization_layout() -> bool:
    """Whether stores live per member here (not a laptop or an ``OPENSRE_MEMORY_DIR`` store)."""
    return (
        not os.getenv(OPENSRE_MEMORY_DIR_ENV, "").strip()
        and (deployment_home() / USERS_DIR_NAME).is_dir()
    )


def member_memory_dirs() -> dict[str, Path]:
    """Every member's memory directory by actor id.

    Outside an organization (a laptop, or ``OPENSRE_MEMORY_DIR`` set) the one
    local store is returned as :data:`LOCAL_ACTOR` when it exists.
    """
    if _organization_layout():
        users = deployment_home() / USERS_DIR_NAME
        found = {
            child.name: directory
            for child in sorted(users.iterdir())
            if child.is_dir() and (directory := _member_dir(child.name)) is not None
        }
        if found:
            return found
    local = get_memory_dir()
    return {LOCAL_ACTOR: local} if local.is_dir() else {}


def member_memory_dir(actor: str) -> Path | None:
    """``actor``'s memory directory in this organization, or ``None`` when it has none."""
    if _organization_layout() and actor != LOCAL_ACTOR:
        return _member_dir(actor)
    return member_memory_dirs().get(actor)


def read_member_store(actor: str, directory: Path) -> MemberStore:
    """The live (unfenced) memories and usage of one member's store."""
    records = tuple(record for record in read_memories(directory) if not is_fenced_record(record))
    return MemberStore(
        actor=actor, directory=directory, records=records, usage=load_usage(directory)
    )


def iter_member_stores() -> Iterator[MemberStore]:
    """Every member's store, in actor order."""
    for actor, directory in member_memory_dirs().items():
        yield read_member_store(actor, directory)


__all__ = [
    "LOCAL_ACTOR",
    "MemberStore",
    "iter_member_stores",
    "member_memory_dir",
    "member_memory_dirs",
    "read_member_store",
]
