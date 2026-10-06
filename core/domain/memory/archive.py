"""Move memories out of the live store into ``.archive/`` instead of deleting them.

Archived files keep their content and frontmatter, so a person can move one
back by hand. The store only parses the top level of the memory directory, so
nothing under ``.archive/`` is ever read as a live memory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.domain.memory.files import ARCHIVE_DIRNAME, ensure_private_subdir, memory_path


def _destination(archive: Path, slug: str, now: datetime) -> Path:
    candidate = archive / f"{slug}.md"
    if not candidate.exists():
        return candidate
    # Archiving the same name twice keeps both copies.
    return archive / f"{slug}.{now.strftime('%Y%m%dT%H%M%S')}.md"


def archive_memory_unlocked(slug: str, *, now: datetime | None = None) -> Path | None:
    """Move ``slug``'s file into the archive; the caller holds the memory lock.

    Returns the archived path, or ``None`` when the memory does not exist.
    May raise ``OSError``.
    """
    source = memory_path(slug)
    if not source.is_file():
        return None
    archive = ensure_private_subdir(ARCHIVE_DIRNAME)
    destination = _destination(archive, slug, now or datetime.now(UTC))
    source.replace(destination)
    return destination


__all__ = ["archive_memory_unlocked"]
