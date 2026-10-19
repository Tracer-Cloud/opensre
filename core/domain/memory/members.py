"""Every member's memory store in this organization, for audits and the web app mirror.

On a hosted gateway each member has ``<org root>/users/<actor id>/memory/``.
Reading one binds the memory engine to it (:func:`memory_root`), so the
caller sees exactly what that member's turns see. Nothing here is reachable
from a turn: a member's turn only ever resolves its own directory.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config.constants import OPENSRE_MEMORY_DIR_ENV
from config.constants.paths import USERS_DIR_NAME, deployment_home, get_memory_dir
from core.domain.memory.files import memory_root
from core.domain.memory.models import MemoryRecord
from core.domain.memory.recall import live_memories
from core.domain.memory.usage import MemoryUsage, load_usage

#: The actor named for a store that is not under ``users/`` (a laptop's own).
LOCAL_ACTOR = "local"
_MEMORY_DIRNAME = "memory"
_SAFE_ACTOR = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class MemberStore:
    """One member's live memories and usage, read with the store bound."""

    actor: str
    directory: Path
    records: tuple[MemoryRecord, ...]
    usage: dict[str, MemoryUsage]


def member_memory_dir(actor: str) -> Path | None:
    """``actor``'s memory directory in this organization, or ``None`` when it has none."""
    return member_memory_dirs().get(actor)


def member_memory_dirs() -> dict[str, Path]:
    """Every member's memory directory by actor id.

    Outside an organization (a laptop, or ``OPENSRE_MEMORY_DIR`` set) the one
    local store is returned as :data:`LOCAL_ACTOR` when it exists.
    """
    users = deployment_home() / USERS_DIR_NAME
    found: dict[str, Path] = {}
    if users.is_dir() and not os.getenv(OPENSRE_MEMORY_DIR_ENV, "").strip():
        for child in sorted(users.iterdir()):
            if child.is_dir() and _SAFE_ACTOR.match(child.name):
                directory = child / _MEMORY_DIRNAME
                if directory.is_dir():
                    found[child.name] = directory
    if found:
        return found
    local = get_memory_dir()
    return {LOCAL_ACTOR: local} if local.is_dir() else {}


def read_member_store(actor: str, directory: Path) -> MemberStore:
    """The live memories and usage of one member's store."""
    with memory_root(directory):
        records = tuple(live_memories())
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
