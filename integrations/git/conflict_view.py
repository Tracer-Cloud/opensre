"""Terminal rendering of the conflicts: a compact overview first, a per-hunk review after.

Before the agent runs, each conflict is one line. After, each conflict gets a
verdict line (kept ours, took theirs, combined, still conflicted); the merged
code is shown only for combined hunks, as a full-width syntax-highlighted
block that keeps its indentation and crops long lines instead of folding them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.console import Group
from rich.padding import Padding
from rich.syntax import Syntax
from rich.text import Text

from integrations.git.conflict_resolution import HunkComparison, MergeConflicts, compare_hunks

_MAX_LINES_PER_SIDE = 30
_HINT_CHARS = 60
_UNRESOLVED = "(still conflicted)"
PENDING = "(resolving)"
_INDENT = 2
_OURS_STYLE = "bold red"
_THEIRS_STYLE = "bold blue"
_MERGED_STYLE = "bold green"
_PENDING_STYLE = "yellow"

KEPT_OURS = "kept ours"
TOOK_THEIRS = "took theirs"
COMBINED = "combined"
REMOVED = "file removed"
DROPPED = "dropped both sides"
STILL_CONFLICTED = "still conflicted"


def verdict(hunk: HunkComparison) -> str:
    """How the hunk ended: kept ours, took theirs, combined, dropped, file removed, or still conflicted."""
    if hunk.result is None:
        return STILL_CONFLICTED
    if hunk.file_removed:
        return REMOVED
    if not hunk.result and (hunk.ours or hunk.theirs):
        return DROPPED
    if tuple(hunk.result) == tuple(hunk.ours):
        return KEPT_OURS
    if tuple(hunk.result) == tuple(hunk.theirs):
        return TOOK_THEIRS
    return COMBINED


def render_overview(
    console: Any, comparisons: Sequence[HunkComparison], *, ours: str, theirs: str
) -> None:
    """One line per file and per conflict, before anything is resolved."""
    heading = Text("Merging ", style="dim")
    heading.append(theirs, style=_THEIRS_STYLE)
    heading.append(" into ", style="dim")
    heading.append(ours, style=_OURS_STYLE)
    console.print(heading)
    for path in _paths_in_order(comparisons):
        hunks = [c for c in comparisons if c.path == path]
        console.print(_file_title(path, len(hunks)))
        for number, hunk in enumerate(hunks, start=1):
            line = Text("  ")
            line.append(f"conflict {number}", style="dim")
            line.append("  ours ", style=_OURS_STYLE)
            line.append(_hint(hunk.ours))
            line.append("  theirs ", style=_THEIRS_STYLE)
            line.append(_hint(hunk.theirs))
            console.print(line)


def render_review(
    console: Any, comparisons: Sequence[HunkComparison], *, ours: str, theirs: str
) -> None:
    """A verdict per conflict; merged code for combined hunks, both sides for open ones."""
    for path in _paths_in_order(comparisons):
        hunks = [c for c in comparisons if c.path == path]
        console.print(_file_title(path, len(hunks)))
        for number, hunk in enumerate(hunks, start=1):
            console.print(_verdict_view(path, number, hunk, ours, theirs))


def _verdict_view(path: str, number: int, hunk: HunkComparison, ours: str, theirs: str) -> Group:
    outcome = verdict(hunk)
    line = Text("  ")
    line.append(f"conflict {number}", style="dim")
    line.append("  ")
    if outcome == KEPT_OURS:
        line.append(f"{KEPT_OURS} ({ours})", style=_OURS_STYLE)
        line.append(f"  {_hint(hunk.ours)}")
        return Group(line)
    if outcome == TOOK_THEIRS:
        line.append(f"{TOOK_THEIRS} ({theirs})", style=_THEIRS_STYLE)
        line.append(f"  {_hint(hunk.theirs)}")
        return Group(line)
    if outcome == STILL_CONFLICTED:
        line.append(STILL_CONFLICTED, style=_PENDING_STYLE)
        return Group(
            line,
            *_side(f"ours · {ours}", _OURS_STYLE, hunk.ours, path),
            *_side(f"theirs · {theirs}", _THEIRS_STYLE, hunk.theirs, path),
        )
    if outcome in (REMOVED, DROPPED):
        line.append(outcome, style=_MERGED_STYLE)
        return Group(line)
    line.append(COMBINED, style=_MERGED_STYLE)
    return Group(line, *_side("merged", _MERGED_STYLE, hunk.result or (), path))


def _file_title(path: str, count: int) -> Text:
    title = Text()
    title.append(path, style="bold")
    title.append(f" · {count} conflict{'s' if count != 1 else ''}", style="dim")
    return title


def _hint(lines: Sequence[str]) -> str:
    first = next((line.strip() for line in lines if line.strip()), "")
    more = sum(1 for line in lines if line.strip()) - 1
    text = first if len(first) <= _HINT_CHARS else first[: _HINT_CHARS - 1] + "…"
    return f"{text} (+{more} lines)" if more > 0 else text or "(empty)"


def render_comparison(
    console: Any,
    comparisons: Sequence[HunkComparison],
    *,
    ours: str,
    theirs: str,
    pending_label: str = _UNRESOLVED,
) -> None:
    """Print every conflict hunk of every file as stacked ours / theirs / merged blocks."""
    for path in _paths_in_order(comparisons):
        hunks = [c for c in comparisons if c.path == path]
        for number, hunk in enumerate(hunks, start=1):
            console.print(_hunk_view(path, number, len(hunks), hunk, ours, theirs, pending_label))


def comparison_text(comparisons: Sequence[HunkComparison], *, ours: str, theirs: str) -> str:
    """Plain-text equivalent for surfaces without a console."""
    lines: list[str] = []
    for path in _paths_in_order(comparisons):
        for number, hunk in enumerate([c for c in comparisons if c.path == path], start=1):
            lines.append(f"{path} conflict {number}")
            lines.append(f"  {ours}:")
            lines.extend(f"    {line}" for line in _clip(hunk.ours))
            lines.append(f"  {theirs}:")
            lines.extend(f"    {line}" for line in _clip(hunk.theirs))
            lines.append("  merged:")
            if hunk.result is None:
                lines.append(f"    {_UNRESOLVED}")
            else:
                lines.extend(f"    {line}" for line in _clip(hunk.result))
    return "\n".join(lines)


def _hunk_view(
    path: str,
    number: int,
    total: int,
    hunk: HunkComparison,
    ours: str,
    theirs: str,
    pending_label: str,
) -> Group:
    title = Text()
    title.append(path, style="bold")
    title.append(f" · conflict {number} of {total}", style="dim")
    parts: list[Any] = [Text(""), title]
    parts.extend(_side(f"ours · {ours}", _OURS_STYLE, hunk.ours, path))
    parts.extend(_side(f"theirs · {theirs}", _THEIRS_STYLE, hunk.theirs, path))
    if hunk.result is None:
        parts.append(
            Padding(Text(f"merged · {pending_label}", style=_PENDING_STYLE), (0, 0, 0, _INDENT))
        )
    else:
        parts.extend(_side("merged", _MERGED_STYLE, hunk.result, path))
    return Group(*parts)


def _side(label: str, style: str, lines: Sequence[str], path: str) -> list[Any]:
    shown = _clip(lines)
    code = "\n".join(_dedent(shown)) if shown else "(empty)"
    block = Syntax(
        code,
        Syntax.guess_lexer(path),
        theme="ansi_dark",
        word_wrap=False,
        background_color="default",
    )
    return [
        Padding(Text(label, style=style), (0, 0, 0, _INDENT)),
        Padding(block, (0, 0, 0, _INDENT * 2)),
    ]


def _dedent(lines: Sequence[str]) -> list[str]:
    """Drop the indentation common to every non-empty line so short hunks start at the margin."""
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    common = min(indents) if indents else 0
    return [line[common:] if line.strip() else "" for line in lines]


def resolution_lines(workspace: str, merge_sha: str, conflicts: MergeConflicts) -> tuple[str, ...]:
    """One line per resolved file: how many conflicts and the verdict of each, read from the commit."""
    comparisons = compare_hunks(workspace, conflicts, revision=merge_sha)
    lines: list[str] = []
    for path in conflicts.names:
        verdicts = [verdict(c) for c in comparisons if c.path == path]
        if not verdicts:
            lines.append(f"{path}: resolved")
            continue
        parts = ", ".join(f"conflict {i} {v}" for i, v in enumerate(verdicts, start=1))
        count = f"{len(verdicts)} conflict{'s' if len(verdicts) != 1 else ''}"
        lines.append(f"{path}: {count} ({parts})")
    return tuple(lines)


def _paths_in_order(comparisons: Sequence[HunkComparison]) -> list[str]:
    seen: dict[str, None] = {}
    for comparison in comparisons:
        seen.setdefault(comparison.path, None)
    return list(seen)


def _clip(lines: Sequence[str]) -> list[str]:
    if len(lines) <= _MAX_LINES_PER_SIDE:
        return list(lines)
    hidden = len(lines) - _MAX_LINES_PER_SIDE
    return [*lines[:_MAX_LINES_PER_SIDE], f"… {hidden} more line{'s' if hidden != 1 else ''}"]


__all__ = [
    "COMBINED",
    "DROPPED",
    "KEPT_OURS",
    "PENDING",
    "REMOVED",
    "STILL_CONFLICTED",
    "TOOK_THEIRS",
    "comparison_text",
    "render_comparison",
    "render_overview",
    "render_review",
    "resolution_lines",
    "verdict",
]
