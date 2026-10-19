"""Combine the notes of memories that say the same thing, never cutting any.

Shared by upkeep (folding a duplicate cluster into the memory it keeps) and
extraction (folding a newly extracted fact into the memory it repeats). A line
already present, ignoring surrounding whitespace, is never written twice; a
merge that would push a body past :data:`MAX_BODY_CHARS` is refused rather
than truncated, so no note is lost.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.domain.memory.models import MAX_BODY_CHARS, MemoryRecord

_SECTION_GAP = "\n\n"


def _without_blank_runs(lines: list[str]) -> list[str]:
    """``lines`` without leading or trailing blank lines and with blank runs collapsed."""
    tidy: list[str] = []
    for line in lines:
        if not line.strip() and (not tidy or not tidy[-1].strip()):
            continue
        tidy.append(line)
    while tidy and not tidy[-1].strip():
        tidy.pop()
    return tidy


def _fresh_lines(text: str, seen: set[str]) -> tuple[list[str], set[str]]:
    """Lines of ``text`` not in ``seen`` (nor repeated within ``text``), and their keys."""
    fresh: list[str] = []
    added: set[str] = set()
    for line in text.strip().splitlines():
        key = line.strip()
        if key in seen or key in added:
            continue
        if key:
            added.add(key)
        fresh.append(line.rstrip())
    return _without_blank_runs(fresh), added


def _seen_lines(body: str) -> set[str]:
    return {line.strip() for line in body.splitlines() if line.strip()}


def merged_body(
    kept: MemoryRecord, duplicates: Sequence[MemoryRecord]
) -> tuple[str, tuple[MemoryRecord, ...]]:
    """``kept``'s body with duplicates' notes appended, and the duplicates that were merged.

    Each merged duplicate gets a ``## Merged from`` heading naming it and the
    day it was last updated. A duplicate whose new lines would not fit is
    left out, so its caller can keep that memory live instead of archiving
    notes that are not in the kept body.
    """
    body = kept.body.strip()
    seen = _seen_lines(body)
    merged: list[MemoryRecord] = []
    for duplicate in duplicates:
        fresh, added = _fresh_lines(duplicate.body, seen)
        heading = f"## Merged from {duplicate.slug} ({duplicate.updated_at[:10]})"
        candidate = _SECTION_GAP.join((body, "\n".join((heading, *fresh))))
        if len(candidate) > MAX_BODY_CHARS:
            continue
        body = candidate
        seen |= added
        merged.append(duplicate)
    return body, tuple(merged)


def folded_body(existing: str, update: str) -> str | None:
    """``existing`` with the lines of ``update`` it lacks appended.

    ``existing`` unchanged when ``update`` adds nothing; ``None`` when the
    combined body would not fit, so the caller can save ``update`` on its own.
    """
    body = existing.strip()
    fresh, _added = _fresh_lines(update, _seen_lines(body))
    if not any(line.strip() for line in fresh):
        return body
    candidate = _SECTION_GAP.join((body, "\n".join(fresh))) if body else "\n".join(fresh)
    return candidate if len(candidate) <= MAX_BODY_CHARS else None


__all__ = ["folded_body", "merged_body"]
