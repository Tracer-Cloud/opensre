"""What may leave the laptop.

Only conversation history and memory sync. Credentials stay on the machine:
``integrations.json`` holds live provider secrets, ``llm-auth.json`` holds model
keys, the keyring fallback file holds whatever the OS keychain could not, and
none of them is needed to resume a conversation elsewhere — a second machine
re-runs the integration wizard.

The rule is an allowlist of roots rather than a deny-list of filenames, so a
file added to ``~/.opensre`` later is excluded by default instead of silently
uploaded. The denied names below are a second, redundant check on top of that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.constants.paths import get_memory_dir
from config.constants.secrets import CREDENTIAL_FALLBACK_FILENAME
from core.agent_harness.spi.defaults import sessions_dir
from infrastructure.filestorage.enums import SyncRootName

# Never uploaded, whatever else changes. Redundant with the allowlist of roots
# and kept that way: two independent checks, so one mistake is not enough.
DENIED_FILENAMES = frozenset(
    {
        "integrations.json",
        "llm-auth.json",
        "opensre.json",
        "config.yml",
        "anonymous_id",
        CREDENTIAL_FALLBACK_FILENAME,
    }
)


@dataclass(frozen=True)
class SyncRoot:
    """One local directory and the key prefix it maps to in the bucket.

    Product roots use :class:`SyncRootName`. Tests may inject other string
    names when exercising the engine with a custom root set.
    """

    name: SyncRootName | str
    path: Path


@dataclass(frozen=True)
class ResolvedRoots:
    """Synced directories with their real paths worked out once.

    Resolving a path hits the filesystem, and the upload loop asks about every
    session and memory file, so the roots are resolved before the loop rather
    than inside it.
    """

    paths: tuple[Path, ...]

    def contains(self, path: Path) -> bool:
        """Whether ``path`` sits in a synced root and is not a denied file."""
        if path.name in DENIED_FILENAMES:
            return False
        candidate = path.resolve()
        return any(candidate == root or root in candidate.parents for root in self.paths)


def syncable_roots() -> tuple[SyncRoot, ...]:
    """Directories that mirror to the bucket, resolved for the current scope."""
    return (
        SyncRoot(name=SyncRootName.SESSIONS, path=sessions_dir()),
        SyncRoot(name=SyncRootName.MEMORY, path=get_memory_dir()),
    )


def resolved_roots(roots: tuple[SyncRoot, ...] | None = None) -> ResolvedRoots:
    """Resolve the synced roots once, for repeated membership checks."""
    source = roots if roots is not None else syncable_roots()
    return ResolvedRoots(paths=tuple(root.path.resolve() for root in source))


def is_syncable(path: Path, *, roots: tuple[SyncRoot, ...] | None = None) -> bool:
    """Whether ``path`` is inside a synced root and not a denied file."""
    return resolved_roots(roots).contains(path)


__all__ = [
    "DENIED_FILENAMES",
    "ResolvedRoots",
    "SyncRoot",
    "SyncRootName",
    "is_syncable",
    "resolved_roots",
    "syncable_roots",
]
