"""How often each memory reached the model, kept beside the memories in ``.usage.json``.

A memory counts as used when its full text is put in front of the model: in
the per-turn RELEVANT MEMORIES block or in a ``memory_recall`` result. Index
order and age-based archiving read these counts. Recording is best-effort and
never blocks a turn for long: the write gives up after a short lock wait.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import Timeout

from core.domain.memory.files import (
    USAGE_FILENAME,
    memory_dir,
    memory_lock,
    read_json_object,
    write_json_atomically,
)

logger = logging.getLogger(__name__)

#: A turn waits at most this long for the directory lock before skipping the update.
_USAGE_LOCK_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class MemoryUsage:
    """Use count and last use (ISO-8601, UTC) of one memory."""

    use_count: int = 0
    last_used_at: str = ""


def _usage_path(directory: Path) -> Path:
    return directory / USAGE_FILENAME


def _parse(raw: Mapping[str, object]) -> dict[str, MemoryUsage]:
    usage: dict[str, MemoryUsage] = {}
    for slug, entry in raw.items():
        if not isinstance(entry, Mapping):
            continue
        count = entry.get("use_count")
        last = entry.get("last_used_at")
        usage[slug] = MemoryUsage(
            use_count=count if isinstance(count, int) and count >= 0 else 0,
            last_used_at=last if isinstance(last, str) else "",
        )
    return usage


def load_usage(directory: Path | None = None) -> dict[str, MemoryUsage]:
    """Usage per slug for ``directory`` (default: the current memory directory)."""
    return _parse(read_json_object(_usage_path(directory or memory_dir())))


def _serialize(usage: Mapping[str, MemoryUsage]) -> dict[str, object]:
    return {
        slug: {"use_count": entry.use_count, "last_used_at": entry.last_used_at}
        for slug, entry in sorted(usage.items())
    }


def record_memory_usage(slugs: Iterable[str], *, now: datetime | None = None) -> None:
    """Count one use of each slug; never raises and never waits long for the lock."""
    unique = list(dict.fromkeys(slug for slug in slugs if slug))
    if not unique:
        return
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    try:
        with memory_lock(timeout=_USAGE_LOCK_TIMEOUT_SECONDS):
            directory = memory_dir()
            usage = load_usage(directory)
            for slug in unique:
                previous = usage.get(slug, MemoryUsage())
                usage[slug] = MemoryUsage(use_count=previous.use_count + 1, last_used_at=stamp)
            write_json_atomically(_usage_path(directory), _serialize(usage))
    except (Timeout, OSError):
        logger.debug("[memory] usage update skipped", exc_info=True)


def prune_usage_unlocked(directory: Path, live_slugs: Iterable[str]) -> None:
    """Drop usage of memories that no longer exist; the caller holds the memory lock."""
    keep = set(live_slugs)
    usage = load_usage(directory)
    pruned = {slug: entry for slug, entry in usage.items() if slug in keep}
    if len(pruned) != len(usage):
        write_json_atomically(_usage_path(directory), _serialize(pruned))


__all__ = ["MemoryUsage", "load_usage", "prune_usage_unlocked", "record_memory_usage"]
