"""Filesystem CRUD for long-term memories under ``~/.opensre/memory/``.

One markdown file per memory plus a generated ``MEMORY.md`` index. Prompt
injection and ``ensure_memory_store`` create the directory eagerly; writes also
create it lazily. Write failures (disk full, permissions) are reported to
stderr and surfaced as ``None``/``False`` results rather than exceptions.

Mutating operations serialize through a directory-scoped ``FileLock`` so
concurrent ``memory_remember`` / forget calls cannot silently overwrite each
other or race the index rebuild. The lock is not reentrant across lock
objects, so code that already holds it uses the ``*_unlocked`` helpers.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from filelock import Timeout

from core.domain.memory.consolidation_state import record_forget_unlocked
from core.domain.memory.fence import is_fenced_record
from core.domain.memory.files import (
    INDEX_FILENAME,
    RESERVED_FILENAMES,
    ensure_memory_dir,
    memory_dir,
    memory_lock,
    memory_path,
    write_text_atomically,
)
from core.domain.memory.frontmatter import parse_memory_file, serialize_memory
from core.domain.memory.index import write_index
from core.domain.memory.merge import folded_body
from core.domain.memory.models import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_EVIDENCE_CHARS,
    TRUNCATION_MARKER,
    MemoryRecord,
    MemorySource,
    MemoryType,
)
from core.domain.memory.safety import find_memory_safety_issues
from core.domain.memory.similarity import closest_duplicate
from core.domain.memory.slugs import is_valid_slug
from core.domain.memory.summaries import remove_memory_summary_unlocked

#: Distinct memory stores kept parsed at once. Each Slack user in an org has
#: their own memory directory, so a single entry would make concurrent users
#: evict each other on every turn. Bounded so the cache cannot grow with the
#: number of users a gateway has ever served.
PARSED_STORE_CACHE_SIZE = 16


def ensure_memory_store() -> Path:
    """Create the memory directory and an empty ``MEMORY.md`` when absent.

    Lives here rather than beside the other path helpers because seeding the
    index needs :mod:`core.domain.memory.index`, and the file primitives must
    not depend on anything above them.

    Safe to call on every chat turn / ``/memory`` invocation — mkdir is
    idempotent and the index file is only seeded once.
    """
    directory = ensure_memory_dir()
    index_path = directory / INDEX_FILENAME
    if not index_path.exists():
        with contextlib.suppress(OSError):
            write_index(directory, [])
    return directory


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _clean_body(body: str) -> str:
    clean = body.strip()
    if len(clean) > MAX_BODY_CHARS:
        clean = clean[: MAX_BODY_CHARS - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return clean


def build_record(
    *,
    slug: str,
    memory_type: MemoryType | str,
    description: str,
    body: str,
    created_at: str,
    updated_at: str,
    source: MemorySource | str | None = None,
    evidence: str = "",
    verified: bool | None = None,
) -> MemoryRecord:
    """A validated, sanitized record ready to write; raises ``ValueError`` when unsafe.

    Descriptions and evidence are flattened to one line and capped; bodies are
    capped. Secret-shaped content in any of them is rejected.
    """
    if not is_valid_slug(slug):
        raise ValueError(f"invalid memory slug: {slug!r}")
    clean_description = _single_line(description)[:MAX_DESCRIPTION_CHARS]
    clean_evidence = _single_line(evidence)[:MAX_EVIDENCE_CHARS]
    clean_body = _clean_body(body)
    issues = find_memory_safety_issues(clean_description, clean_body, clean_evidence)
    if issues:
        raise ValueError(
            "memory content rejected by safety checks: " + ",".join(issue.rule for issue in issues)
        )
    return MemoryRecord(
        slug=slug,
        memory_type=MemoryType(memory_type),
        description=clean_description,
        created_at=created_at,
        updated_at=updated_at,
        body=clean_body,
        source=MemorySource(source) if source is not None else None,
        evidence=clean_evidence,
        verified=verified,
    )


def write_record_unlocked(record: MemoryRecord) -> None:
    """Write one memory file; the caller holds the memory lock. May raise ``OSError``."""
    ensure_memory_dir()
    write_text_atomically(memory_path(record.slug), serialize_memory(record))


def _candidate(
    *,
    slug: str,
    memory_type: MemoryType | str,
    description: str,
    body: str,
    source: MemorySource | str | None,
    evidence: str,
    verified: bool | None,
) -> MemoryRecord:
    """The record to write, validated, with its stamps left for the write to set."""
    return build_record(
        slug=slug,
        memory_type=memory_type,
        description=description,
        body=body,
        created_at="",
        updated_at="",
        source=source,
        evidence=evidence,
        verified=verified,
    )


def _locked_save[T](slug: str, write: Callable[[], T]) -> T | None:
    """Run ``write`` under the memory lock; ``None`` (reported) on a lock timeout or I/O error."""
    try:
        with memory_lock():
            return write()
    except Timeout:
        print(f"[memory] timed out acquiring lock to save memory {slug!r}", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"[memory] failed to save memory {slug!r}: {exc}", file=sys.stderr)
        return None


def _place_unlocked(candidate: MemoryRecord) -> tuple[MemoryRecord, bool]:
    """Write ``candidate`` under its own name; ``(record, created)``. The caller holds the lock."""
    existing = load_memory(candidate.slug)
    now = _now_iso()
    record = replace(
        candidate,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    write_record_unlocked(record)
    rebuild_index_best_effort()
    return record, existing is None


def save_memory(
    *,
    slug: str,
    memory_type: MemoryType | str,
    description: str,
    body: str,
    source: MemorySource | str | None = None,
    evidence: str = "",
    verified: bool | None = None,
) -> tuple[MemoryRecord, bool] | None:
    """Create or update a memory; returns ``(record, created)`` or ``None`` on I/O failure.

    Updates preserve ``created_at`` from the existing file; provenance is the
    latest write's. The ``MEMORY.md`` index is rebuilt after every successful
    write. Read-modify-write runs under the memory-directory lock so parallel
    writers to the same slug cannot both report ``created=True`` or discard
    each other's content. Raises ``ValueError`` for an invalid slug or unsafe
    content.
    """
    candidate = _candidate(
        slug=slug,
        memory_type=memory_type,
        description=description,
        body=body,
        source=source,
        evidence=evidence,
        verified=verified,
    )
    return _locked_save(slug, lambda: _place_unlocked(candidate))


def _stronger_provenance(
    kept: MemoryRecord, update: MemoryRecord
) -> tuple[MemorySource | None, str, bool | None] | None:
    """The update's provenance when it is better supported than the kept memory's, else ``None``."""
    if (update.verified and not kept.verified) or (kept.source is None and update.source):
        return update.source, update.evidence, update.verified
    return None


def _merge_target(candidate: MemoryRecord) -> tuple[MemoryRecord, str] | None:
    """The live memory ``candidate`` repeats and the combined body, or ``None``."""
    duplicate = closest_duplicate(
        candidate.slug,
        candidate.memory_type,
        candidate.description,
        [record for record in list_memories() if not is_fenced_record(record)],
    )
    if duplicate is None:
        return None
    folded = folded_body(duplicate.body, candidate.body)
    return (duplicate, folded) if folded is not None else None


def _fold_unlocked(candidate: MemoryRecord) -> tuple[MemoryRecord, str]:
    """Write ``candidate``, folding it into the memory it repeats; the caller holds the lock."""
    target = _merge_target(candidate) if load_memory(candidate.slug) is None else None
    if target is None:
        record, created = _place_unlocked(candidate)
        return record, "created" if created else "updated"
    duplicate, folded = target
    provenance = _stronger_provenance(duplicate, candidate)
    if folded == duplicate.body.strip() and provenance is None:
        return duplicate, "unchanged"
    source, evidence, verified = provenance or (
        duplicate.source,
        duplicate.evidence,
        duplicate.verified,
    )
    record = build_record(
        slug=duplicate.slug,
        memory_type=duplicate.memory_type,
        description=duplicate.description,
        body=folded,
        created_at=duplicate.created_at,
        updated_at=_now_iso(),
        source=source,
        evidence=evidence,
        verified=verified,
    )
    write_record_unlocked(record)
    rebuild_index_best_effort()
    return record, "merged"


def save_memory_merging_duplicates(
    *,
    slug: str,
    memory_type: MemoryType | str,
    description: str,
    body: str,
    source: MemorySource | str | None = None,
    evidence: str = "",
    verified: bool | None = None,
) -> tuple[MemoryRecord, str] | None:
    """:func:`save_memory` that folds a near-duplicate into the memory it repeats.

    For writers that pick a fresh name for a fact already stored (background
    extraction). A new name whose header says what a live memory of the same
    type says (:func:`~core.domain.memory.similarity.closest_duplicate`) is
    written into that memory: its name, description and creation time are
    kept and the update's new lines are appended. Returns ``(record, outcome)``
    with outcome ``created``, ``updated``, ``merged`` or ``unchanged`` (the
    update added nothing), or ``None`` on I/O failure. Raises ``ValueError``
    like :func:`save_memory`.
    """
    candidate = _candidate(
        slug=slug,
        memory_type=memory_type,
        description=description,
        body=body,
        source=source,
        evidence=evidence,
        verified=verified,
    )
    return _locked_save(slug, lambda: _fold_unlocked(candidate))


def load_memory(slug: str) -> MemoryRecord | None:
    if not is_valid_slug(slug):
        return None
    try:
        text = memory_path(slug).read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_memory_file(text)


def _memory_files(directory: Path) -> list[Path]:
    """Top-level memory files only: the archive and session summaries live in subfolders."""
    return [path for path in sorted(directory.glob("*.md")) if path.name not in RESERVED_FILENAMES]


def memory_dir_signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """Name, mtime and size of every memory file — cheap enough to check per turn.

    Directory mtime alone is not enough: editing a memory in place leaves it
    unchanged, so the cache would serve a stale body. Per-file size and mtime
    move on any edit.
    """
    entries: list[tuple[str, int, int]] = []
    for path in _memory_files(directory):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


@lru_cache(maxsize=PARSED_STORE_CACHE_SIZE)
def parsed_memories(
    directory_key: str,
    _signature: tuple[tuple[str, int, int], ...],
) -> tuple[MemoryRecord, ...]:
    """Parse every memory file under ``directory_key``, most recently updated first.

    Keyed by the directory *and* its file signature. The directory is part of
    the key because memory stores are per-principal — one Slack user must
    never be served another's memories, however alike the two stores look.
    The signature is part of the key so an edited memory is re-read.
    """
    records: list[MemoryRecord] = []
    for path in _memory_files(Path(directory_key)):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        record = parse_memory_file(text)
        if record is not None:
            records.append(record)
    records.sort(key=lambda r: r.updated_at, reverse=True)
    return tuple(records)


def read_memories(directory: Path) -> tuple[MemoryRecord, ...]:
    """Every parseable memory under ``directory``, most recently updated first, uncached.

    For whole-organization passes (the audit, the web app mirror): read
    through :func:`parsed_memories` they would evict the stores members'
    turns are using from its small cache, one member after another.
    """
    return parsed_memories.__wrapped__(str(directory), ())


def list_memories() -> list[MemoryRecord]:
    """All parseable live memories, most recently updated first.

    Every action-agent turn renders the memory index, so this would otherwise
    read and parse the whole store on each turn — a cost that grows with the
    number of memories a user has accumulated. Parsed records are reused until
    the files change.
    """
    directory = memory_dir()
    if not directory.is_dir():
        return []
    return list(parsed_memories(str(directory), memory_dir_signature(directory)))


def delete_memory(slug: str) -> bool:
    """Delete one memory; ``True`` when a file was removed.

    A forgotten fact must not come back through the summary. This drops
    ``memory_summary.md`` and the consolidation cooldown, so the next
    consolidation writes a fresh summary, and records when the forget
    happened: session summaries recorded until then may describe the fact,
    so consolidation never reads them again.
    """
    if not is_valid_slug(slug):
        return False
    path = memory_path(slug)
    try:
        with memory_lock():
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            directory = memory_dir()
            remove_memory_summary_unlocked(directory)
            record_forget_unlocked(directory)
            rebuild_index_best_effort()
            return True
    except Timeout:
        print(f"[memory] timed out acquiring lock to delete memory {slug!r}", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"[memory] failed to delete memory {slug!r}: {exc}", file=sys.stderr)
        return False


def rebuild_index() -> Path:
    """Rewrite MEMORY.md from a fresh directory scan. May raise ``OSError``."""
    return write_index(ensure_memory_dir(), list_memories())


def rebuild_index_best_effort() -> None:
    """:func:`rebuild_index`, reporting failure to stderr instead of raising."""
    # A failed rebuild leaves MEMORY.md stale until the next successful write.
    # That is tolerable because MEMORY.md is a human-facing convenience only:
    # every agent-facing read (list_memories, render_prompt_index) scans the
    # directory directly, so a stale index never affects recall or extraction.
    try:
        rebuild_index()
    except OSError as exc:
        print(f"[memory] failed to rebuild {INDEX_FILENAME}: {exc}", file=sys.stderr)


__all__ = [
    "PARSED_STORE_CACHE_SIZE",
    "build_record",
    "delete_memory",
    "ensure_memory_store",
    "list_memories",
    "load_memory",
    "memory_dir",
    "memory_dir_signature",
    "memory_path",
    "parsed_memories",
    "rebuild_index",
    "read_memories",
    "rebuild_index_best_effort",
    "save_memory",
    "save_memory_merging_duplicates",
    "write_record_unlocked",
]
