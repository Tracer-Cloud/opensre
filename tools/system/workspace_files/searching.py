"""Search file contents with a regular expression, one bounded page of matches."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from infrastructure.safety.secret_redaction import redact_text
from tools.system.workspace_files.access import MAX_SEARCH_BYTES, FileAccessError, decode_text
from tools.system.workspace_files.reading import text_lines

#: Matching lines one search returns; a caller narrows the search to see others.
DEFAULT_MATCH_LIMIT = 100
#: Lines of context a caller may ask for on each side of a match.
MAX_CONTEXT_LINES = 10
#: A matching line is shown up to this length.
MAX_MATCH_CHARS = 400


@dataclass(frozen=True)
class SearchPage:
    """Matches in path order, grouped by file, and whether more exist."""

    lines: tuple[str, ...]
    match_count: int
    file_count: int
    truncated: bool


def matching_lines(
    files: Iterable[tuple[str, Path]],
    pattern: re.Pattern[str],
    *,
    shown_path: Callable[[str], str],
    limit: int = DEFAULT_MATCH_LIMIT,
    context: int = 0,
) -> SearchPage:
    """Lines matching ``pattern`` in ``files``, as ``path:line:text``.

    Stops at ``limit`` matches and says so, rather than counting a total no
    caller reads. Context lines are rendered ``path-line-text``; a ``--`` line
    separates groups that are not adjacent. Unreadable, binary and oversized
    files are skipped: a search reports what it could read. Matching runs on
    the file as written; every line returned is redacted.
    """
    limit = max(limit, 1)
    context = min(max(context, 0), MAX_CONTEXT_LINES)
    out: list[str] = []
    matches = 0
    matched_files = 0
    for name, target in files:
        try:
            text = decode_text(target, max_bytes=MAX_SEARCH_BYTES)
        except FileAccessError:
            continue
        if pattern.search(text) is None:
            continue
        lines = text_lines(text)
        hits = [index for index, line in enumerate(lines) if pattern.search(line)]
        if not hits:
            # The pattern only matches across a line break; nothing to cite.
            continue
        remaining = limit - matches
        truncated = len(hits) > remaining
        hits = hits[:remaining]
        matched_files += 1
        matches += len(hits)
        _render_file(out, shown_path(name), lines, hits, context)
        if truncated or matches >= limit:
            return SearchPage(tuple(out), matches, matched_files, truncated=True)
    return SearchPage(tuple(out), matches, matched_files, truncated=False)


def _render_file(
    out: list[str], path: str, lines: list[str], hits: list[int], context: int
) -> None:
    """Append one file's matches, with their context windows merged where they touch."""
    hit_set = set(hits)
    previous = -2
    for index in sorted({near for hit in hits for near in _window(hit, context, len(lines))}):
        if context and out and index != previous + 1:
            out.append("--")
        mark = ":" if index in hit_set else "-"
        out.append(f"{path}{mark}{index + 1}{mark}{_cut(lines[index])}")
        previous = index


def _window(hit: int, context: int, length: int) -> range:
    return range(max(hit - context, 0), min(hit + context + 1, length))


def _cut(line: str) -> str:
    """One line as it is shown: credentials redacted, then bounded."""
    stripped = redact_text(line.rstrip())
    return stripped if len(stripped) <= MAX_MATCH_CHARS else stripped[:MAX_MATCH_CHARS] + "…"


__all__ = [
    "DEFAULT_MATCH_LIMIT",
    "MAX_CONTEXT_LINES",
    "SearchPage",
    "matching_lines",
]
