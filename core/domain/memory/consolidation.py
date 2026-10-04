"""Periodic tidy-up of the memory store, at most once every six hours.

Two steps:

1. **Deterministic**, under the memory lock: archive fenced demo/sample
   memories and memories neither updated nor used for 120 days (``user`` and
   ``preference`` memories never age out), and merge repository memories
   about the same ``owner/repo`` — the most recently updated one is kept, gains
   a one-line pointer to the other, and the other is archived.
2. **Summary**, optional: an injected summarizer (an LLM on the harness side)
   turns the live memories and recent session summaries into
   ``memory_summary.md``. The lock is not held while it runs, and it is skipped
   when its input has not changed since the last summary.

Nothing is deleted: archived files move to ``.archive/``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import Timeout

from core.domain.memory.archive import archive_memory_unlocked
from core.domain.memory.consolidation_state import (
    ConsolidationState,
    read_consolidation_state,
    write_consolidation_state_unlocked,
)
from core.domain.memory.fence import is_fenced_record
from core.domain.memory.files import memory_dir, memory_lock
from core.domain.memory.models import PERSONAL_MEMORY_TYPES, MemoryRecord, MemoryType
from core.domain.memory.safety import find_memory_safety_issues, redact_memory_unsafe_text
from core.domain.memory.store import (
    build_record,
    list_memories,
    rebuild_index_best_effort,
    write_record_unlocked,
)
from core.domain.memory.summaries import (
    SessionSummary,
    read_memory_summary,
    recent_session_summaries,
    write_memory_summary_unlocked,
)
from core.domain.memory.usage import MemoryUsage, load_usage, prune_usage_unlocked

logger = logging.getLogger(__name__)

CONSOLIDATION_INTERVAL = timedelta(hours=6)
STALE_AFTER = timedelta(days=120)
#: Session summaries handed to the summarizer, newest first.
RECENT_SESSION_SUMMARIES = 10

# ``owner/repo`` as written in a description or heading; not part of a path or URL.
_REPOSITORY_ID_RE = re.compile(
    r"(?<![\w./:@-])([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})(?![\w/-])"
)
_NOT_REPOSITORIES = frozenset({"and/or", "client/server", "input/output", "read/write"})
_SUBJECT_BODY_LINES = 3


@dataclass(frozen=True)
class ConsolidationInput:
    """What the summarizer reads: live memories, recent sessions and the summary it replaces."""

    memories: tuple[MemoryRecord, ...]
    session_summaries: tuple[SessionSummary, ...]
    current_summary: str


type Summarizer = Callable[[ConsolidationInput], str]


@dataclass(frozen=True)
class ConsolidationResult:
    """What one call did; ``ran`` is ``False`` when the cooldown or the lock stopped it."""

    ran: bool
    archived: tuple[str, ...] = ()
    #: ``(archived duplicate, kept memory)`` pairs.
    merged: tuple[tuple[str, str], ...] = ()
    summary_written: bool = False


def _parse_stamp(value: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _is_stale(record: MemoryRecord, usage: MemoryUsage, now: datetime) -> bool:
    if record.memory_type in PERSONAL_MEMORY_TYPES:
        return False
    stamps = [
        stamp
        for stamp in (_parse_stamp(record.updated_at), _parse_stamp(usage.last_used_at))
        if stamp is not None
    ]
    return bool(stamps) and now - max(stamps) > STALE_AFTER


def repository_subject(record: MemoryRecord) -> str | None:
    """The ``owner/repo`` a repository memory is about, lowercased; ``None`` when unclear.

    The first identifier in the description wins; otherwise the first in the
    opening lines of the body (usually a ``# owner/repo`` heading).
    """
    if record.memory_type is not MemoryType.REPOSITORY:
        return None
    opening = [line for line in record.body.splitlines() if line.strip()][:_SUBJECT_BODY_LINES]
    for text in (record.description, *opening):
        for owner, name in _REPOSITORY_ID_RE.findall(text):
            if owner.isupper() and name.isupper():
                continue  # CI/CD, I/O and other acronyms
            candidate = f"{owner}/{name.rstrip('.')}".lower()
            if candidate not in _NOT_REPOSITORIES:
                return candidate
    return None


def _pointer_line(duplicate: MemoryRecord) -> str:
    return (
        f"- Merged duplicate `{duplicate.slug}` (updated {duplicate.updated_at[:10]}, "
        f"archived in .archive/): {duplicate.description}"
    )


def _merge_duplicates_unlocked(records: list[MemoryRecord], now: datetime) -> list[tuple[str, str]]:
    """Keep the newest memory per repository subject and archive the others."""
    groups: dict[str, list[MemoryRecord]] = {}
    for record in records:
        subject = repository_subject(record)
        if subject is not None:
            groups.setdefault(subject, []).append(record)
    merged: list[tuple[str, str]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda record: (record.updated_at, record.slug), reverse=True)
        kept, duplicates = group[0], group[1:]
        pointers = "\n".join(_pointer_line(duplicate) for duplicate in duplicates)
        try:
            combined = build_record(
                slug=kept.slug,
                memory_type=kept.memory_type,
                description=kept.description,
                body=f"{kept.body.rstrip()}\n\n{pointers}",
                created_at=kept.created_at,
                updated_at=now.isoformat(timespec="seconds"),
                source=kept.source,
                evidence=kept.evidence,
                verified=kept.verified,
            )
        except ValueError:
            continue
        write_record_unlocked(combined)
        for duplicate in duplicates:
            if archive_memory_unlocked(duplicate.slug, now=now) is not None:
                merged.append((duplicate.slug, kept.slug))
    return merged


def _tidy_unlocked(directory: Path, now: datetime) -> tuple[list[str], list[tuple[str, str]]]:
    usage = load_usage(directory)
    archived: list[str] = []
    remaining: list[MemoryRecord] = []
    for record in list_memories():
        if is_fenced_record(record) or _is_stale(
            record, usage.get(record.slug, MemoryUsage()), now
        ):
            if archive_memory_unlocked(record.slug, now=now) is not None:
                archived.append(record.slug)
            continue
        remaining.append(record)
    merged = _merge_duplicates_unlocked(remaining, now)
    if archived or merged:
        rebuild_index_best_effort()
        prune_usage_unlocked(directory, (record.slug for record in list_memories()))
    return archived, merged


def _fingerprint(source: ConsolidationInput) -> str:
    digest = hashlib.sha256()
    for record in source.memories:
        digest.update(f"m\0{record.slug}\0{record.updated_at}\n".encode())
    for summary in source.session_summaries:
        digest.update(f"s\0{summary.session_id}\0{summary.recorded_at}\n".encode())
    return digest.hexdigest()


def _summary_input(directory: Path) -> ConsolidationInput:
    return ConsolidationInput(
        memories=tuple(record for record in list_memories() if not is_fenced_record(record)),
        session_summaries=tuple(
            recent_session_summaries(RECENT_SESSION_SUMMARIES, directory=directory)
        ),
        current_summary=read_memory_summary(directory),
    )


def _write_summary(directory: Path, summarize: Summarizer, previous: ConsolidationState) -> bool:
    """Ask the summarizer for a new ``memory_summary.md``; never raises."""
    source = _summary_input(directory)
    if not source.memories and not source.session_summaries:
        return False
    fingerprint = _fingerprint(source)
    if fingerprint == previous.summary_input and source.current_summary:
        return False
    try:
        text = redact_memory_unsafe_text(summarize(source).strip())
    except Exception:  # noqa: BLE001 - the summary is optional; a provider failure skips it
        logger.debug("[memory] consolidation summary failed", exc_info=True)
        return False
    if not text or find_memory_safety_issues(text):
        return False
    try:
        with memory_lock():
            write_memory_summary_unlocked(directory, text)
            state = read_consolidation_state(directory)
            write_consolidation_state_unlocked(directory, replace(state, summary_input=fingerprint))
    except (Timeout, OSError):
        logger.debug("[memory] could not write memory_summary.md", exc_info=True)
        return False
    return True


def _cooling_down(state: ConsolidationState, now: datetime) -> bool:
    last = state.last_run_at
    return last is not None and timedelta(0) <= now - last < CONSOLIDATION_INTERVAL


def consolidate_memories(
    *,
    now: datetime | None = None,
    summarize: Summarizer | None = None,
) -> ConsolidationResult:
    """Tidy the current memory directory unless it was tidied in the last six hours.

    Archives fenced and stale memories, merges duplicate repository memories,
    then (with ``summarize``) refreshes ``memory_summary.md``. Never raises; a
    held lock or an I/O error returns ``ran=False``.
    """
    moment = now or datetime.now(UTC)
    try:
        with memory_lock():
            directory = memory_dir()
            state = read_consolidation_state(directory)
            if _cooling_down(state, moment):
                return ConsolidationResult(ran=False)
            write_consolidation_state_unlocked(directory, replace(state, last_run_at=moment))
            archived, merged = _tidy_unlocked(directory, moment)
    except (Timeout, OSError):
        logger.debug("[memory] consolidation skipped", exc_info=True)
        return ConsolidationResult(ran=False)
    summary_written = (
        _write_summary(directory, summarize, state) if summarize is not None else False
    )
    return ConsolidationResult(
        ran=True,
        archived=tuple(archived),
        merged=tuple(merged),
        summary_written=summary_written,
    )


__all__ = [
    "CONSOLIDATION_INTERVAL",
    "RECENT_SESSION_SUMMARIES",
    "STALE_AFTER",
    "ConsolidationInput",
    "ConsolidationResult",
    "Summarizer",
    "consolidate_memories",
    "repository_subject",
]
