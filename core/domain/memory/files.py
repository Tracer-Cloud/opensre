"""Filesystem path, lock and write primitives for long-term memory.

The lowest layer of the memory package: it knows where files live and how to
write one safely, and imports nothing else from the package. Seeding the index
needs :mod:`core.domain.memory.index`, so it lives in
:mod:`core.domain.memory.store`, which already sits above both.

Layout of the memory directory::

    MEMORY.md              generated index for humans (rebuilt on every write)
    memory_summary.md      consolidated summary, read at the top of the prompt index
    <slug>.md              one live memory per file
    .archive/              archived memories; never parsed as live memory
    sessions/<id>.md       per-session summaries that consolidation reads
    .usage.json            per-memory use counts
    .consolidation.json    when consolidation last ran and a memory was last forgotten
    .memory.lock           directory lock serializing writers
"""

from __future__ import annotations

import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

# Unix permission bits, one octal digit per audience: owner, group, everyone
# else. Each digit adds read (4), write (2) and execute (1).
#
# 0o700 on a directory — owner gets 7 (4+2+1: list it, create and delete entries,
# and enter it), group and everyone else get 0. On a directory the execute bit is
# what allows entering at all, so without it the owner could not reach the files
# inside.
MEMORY_DIR_MODE = 0o700

# 0o600 on a file — owner gets 6 (4+2: read and write), group and everyone else
# get 0. No execute bit: these are notes, never run.
#
# Both are owner-only because memory holds whatever the user told the agent to
# remember, which can include names, hosts and incident detail. On a shared
# machine the default would otherwise leave them world-readable.
MEMORY_FILE_MODE = 0o600

INDEX_FILENAME = "MEMORY.md"
SUMMARY_FILENAME = "memory_summary.md"
ARCHIVE_DIRNAME = ".archive"
SESSIONS_DIRNAME = "sessions"
USAGE_FILENAME = ".usage.json"
CONSOLIDATION_STATE_FILENAME = ".consolidation.json"

#: Markdown files in the memory directory that are not memories.
RESERVED_FILENAMES: frozenset[str] = frozenset({INDEX_FILENAME, SUMMARY_FILENAME})

_LOCK_FILENAME = ".memory.lock"
LOCK_TIMEOUT_SECONDS = 10.0


def memory_dir() -> Path:
    from config.constants import get_memory_dir

    return get_memory_dir()


def memory_path(slug: str) -> Path:
    return memory_dir() / f"{slug}.md"


def ensure_memory_dir() -> Path:
    """Create ``~/.opensre/memory`` (or ``OPENSRE_MEMORY_DIR``) if missing."""
    directory = memory_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=MEMORY_DIR_MODE)
    with contextlib.suppress(OSError):
        directory.chmod(MEMORY_DIR_MODE)
    return directory


def ensure_private_subdir(name: str) -> Path:
    """Create an owner-only subdirectory of the memory directory (``.archive``, ``sessions``)."""
    directory = ensure_memory_dir() / name
    directory.mkdir(parents=True, exist_ok=True, mode=MEMORY_DIR_MODE)
    with contextlib.suppress(OSError):
        directory.chmod(MEMORY_DIR_MODE)
    return directory


def memory_lock(timeout: float = LOCK_TIMEOUT_SECONDS) -> FileLock:
    """The directory lock every memory writer holds.

    Not reentrant across lock objects: a function that holds it must call the
    unlocked variants of other writers rather than their locking entry points.
    """
    directory = ensure_memory_dir()
    return FileLock(str(directory / _LOCK_FILENAME), timeout=timeout)


def write_text_atomically(path: Path, text: str) -> None:
    """Write ``text`` via a same-directory temp file, then replace atomically."""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp_path = Path(tmp.name)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.chmod(MEMORY_FILE_MODE)
            tmp_path.replace(path)
    except OSError:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def read_json_object(path: Path) -> dict[str, Any]:
    """The JSON object stored at ``path``; ``{}`` when missing or unreadable."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Persist ``payload`` as JSON through :func:`write_text_atomically`. May raise ``OSError``."""
    write_text_atomically(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


__all__ = [
    "ARCHIVE_DIRNAME",
    "CONSOLIDATION_STATE_FILENAME",
    "INDEX_FILENAME",
    "LOCK_TIMEOUT_SECONDS",
    "MEMORY_DIR_MODE",
    "MEMORY_FILE_MODE",
    "RESERVED_FILENAMES",
    "SESSIONS_DIRNAME",
    "SUMMARY_FILENAME",
    "USAGE_FILENAME",
    "ensure_memory_dir",
    "ensure_private_subdir",
    "memory_dir",
    "memory_lock",
    "memory_path",
    "read_json_object",
    "write_json_atomically",
    "write_text_atomically",
]
