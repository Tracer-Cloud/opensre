"""Search file contents with a regular expression, one bounded page of matches."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex

from infrastructure.safety.secret_redaction import redact_text_keeping_lines
from tools.system.workspace_files.access import MAX_SEARCH_BYTES, FileAccessError, decode_text
from tools.system.workspace_files.reading import text_lines

#: Matching lines one search returns; a caller narrows the search to see others.
DEFAULT_MATCH_LIMIT = 100
#: Lines of context a caller may ask for on each side of a match.
MAX_CONTEXT_LINES = 10
#: A matching line is shown up to this length.
MAX_MATCH_CHARS = 400
#: Seconds one search may spend matching before it returns what it has.
SEARCH_SECONDS = 10.0
#: The engine refuses a zero deadline; the last call of a spent search gets this.
_SHORTEST_DEADLINE = 0.001


class SearchPatternError(ValueError):
    """The pattern is not a valid regular expression; the message says why."""


class SearchPattern:
    """A compiled search expression whose matching stops at a deadline.

    The pattern is model-written, and the standard library's engine can spend
    hours on one that backtracks, holding the interpreter lock so nothing else
    runs either. This engine takes a time limit and releases the lock.
    """

    def __init__(self, pattern: str, *, ignore_case: bool = False) -> None:
        # MULTILINE: a file is tested whole before its lines are, and ``^`` must
        # mean the start of a line in both.
        flags = regex.MULTILINE | (regex.IGNORECASE if ignore_case else 0)
        try:
            self._compiled: Any = regex.compile(pattern, flags)
        except regex.error as exc:
            raise SearchPatternError(str(exc)) from exc

    def found_in(self, text: str, *, seconds: float) -> bool:
        """Whether the pattern occurs in ``text``; ``TimeoutError`` past ``seconds``."""
        found = self._compiled.search(
            text, timeout=max(seconds, _SHORTEST_DEADLINE), concurrent=True
        )
        return found is not None


@dataclass(frozen=True)
class SearchPage:
    """Matches in path order, grouped by file, and whether the search saw everything."""

    lines: tuple[str, ...]
    match_count: int
    file_count: int
    #: Stopped at the match limit; more may exist.
    truncated: bool = False
    #: Stopped at the time limit; files after the last one shown were not searched.
    timed_out: bool = False


def matching_lines(
    files: Iterable[tuple[str, Path]],
    pattern: SearchPattern,
    *,
    shown_path: Callable[[str], str],
    limit: int = DEFAULT_MATCH_LIMIT,
    context: int = 0,
    seconds: float = SEARCH_SECONDS,
) -> SearchPage:
    """Lines matching ``pattern`` in ``files``, as ``path:line:text``.

    Stops at ``limit`` matches or ``seconds`` of matching and says which,
    rather than counting a total no caller reads. Context lines are rendered
    ``path-line-text``; a ``--`` line separates groups that are not adjacent.
    Unreadable, binary and oversized files are skipped: a search reports what
    it could read.

    A line matches as it is shown, with credentials redacted. Matching the
    file as written would let a caller test a hidden token one prefix at a
    time and read the answer from whether the line came back.
    """
    limit = max(limit, 1)
    context = min(max(context, 0), MAX_CONTEXT_LINES)
    deadline = time.monotonic() + seconds
    out: list[str] = []
    matches = 0
    matched_files = 0
    try:
        for name, target in files:
            try:
                # Lines end at a line feed alone, so ``$`` meets the text a CRLF file shows.
                written = decode_text(target, max_bytes=MAX_SEARCH_BYTES).replace("\r\n", "\n")
            except FileAccessError:
                continue
            # Most files hold no match; only the ones that might are redacted.
            if not pattern.found_in(written, seconds=deadline - time.monotonic()):
                continue
            lines = text_lines(redact_text_keeping_lines(written))
            hits = [
                index
                for index, line in enumerate(lines)
                if pattern.found_in(line, seconds=deadline - time.monotonic())
            ]
            if not hits:
                # The match was inside a credential, or spanned a line break.
                continue
            remaining = limit - matches
            truncated = len(hits) > remaining
            hits = hits[:remaining]
            matched_files += 1
            matches += len(hits)
            _render_file(out, shown_path(name), lines, hits, context)
            if truncated or matches >= limit:
                return SearchPage(tuple(out), matches, matched_files, truncated=True)
    except TimeoutError:
        return SearchPage(tuple(out), matches, matched_files, timed_out=True)
    return SearchPage(tuple(out), matches, matched_files)


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
    """One already-redacted line, bounded for display."""
    stripped = line.rstrip()
    return stripped if len(stripped) <= MAX_MATCH_CHARS else stripped[:MAX_MATCH_CHARS] + "…"


__all__ = [
    "DEFAULT_MATCH_LIMIT",
    "MAX_CONTEXT_LINES",
    "SEARCH_SECONDS",
    "SearchPage",
    "SearchPattern",
    "SearchPatternError",
    "matching_lines",
]
