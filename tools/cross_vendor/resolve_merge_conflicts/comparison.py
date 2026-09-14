"""Terminal rendering of each conflict hunk: our side, their side, and the merged result."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.table import Table
from rich.text import Text

from integrations.git import HunkComparison

_MAX_LINES_PER_CELL = 30
_UNRESOLVED = "(still conflicted)"
PENDING = "(resolving)"


def render_comparison(
    console: Any,
    comparisons: Sequence[HunkComparison],
    *,
    ours: str,
    theirs: str,
    pending_label: str = _UNRESOLVED,
) -> None:
    """Print one three-column table per conflicted file, one row per hunk."""
    for path in _paths_in_order(comparisons):
        hunks = [c for c in comparisons if c.path == path]
        table = Table(
            title=f"{path} · {len(hunks)} conflict{'s' if len(hunks) != 1 else ''}",
            title_justify="left",
            expand=True,
            show_lines=True,
            pad_edge=False,
        )
        table.add_column("#", width=3, no_wrap=True, style="dim")
        table.add_column(f"{ours} (ours)", ratio=1, overflow="fold")
        table.add_column(f"{theirs} (theirs)", ratio=1, overflow="fold")
        table.add_column("merged result", ratio=1, overflow="fold")
        for number, hunk in enumerate(hunks, start=1):
            table.add_row(
                str(number),
                _cell(hunk.ours, style="red"),
                _cell(hunk.theirs, style="blue"),
                _cell(hunk.result, style="green")
                if hunk.result is not None
                else Text(pending_label, style="yellow"),
            )
        console.print(table)


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


def _paths_in_order(comparisons: Sequence[HunkComparison]) -> list[str]:
    seen: dict[str, None] = {}
    for comparison in comparisons:
        seen.setdefault(comparison.path, None)
    return list(seen)


def _cell(lines: Sequence[str], *, style: str) -> Text:
    return Text("\n".join(_clip(lines)) or "(empty)", style=style)


def _clip(lines: Sequence[str]) -> list[str]:
    if len(lines) <= _MAX_LINES_PER_CELL:
        return list(lines)
    hidden = len(lines) - _MAX_LINES_PER_CELL
    return [*lines[:_MAX_LINES_PER_CELL], f"… {hidden} more line{'s' if hidden != 1 else ''}"]


__all__ = ["PENDING", "comparison_text", "render_comparison"]
