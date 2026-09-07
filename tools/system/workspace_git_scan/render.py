"""Terminal rendering of a workspace snapshot: headline counts and a commit bar chart."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from tools.system.workspace_git_scan.scan import WorkspaceSnapshot

_TOP_REPOS = 4
_BAR_MAX_CELLS = 40
_BAR_CELL = "█"
_SERIES_COLOURS = ("green3", "cyan", "blue_violet", "magenta", "hot_pink")
_OTHERS_LABEL = "all others"


def render_snapshot(console: Any, snapshot: WorkspaceSnapshot) -> None:
    """Print the headline counts and the activity bar chart to *console*."""
    console.print(Group(*_headline(snapshot), Text(""), *_bar_chart(snapshot)))


def snapshot_text(snapshot: WorkspaceSnapshot) -> str:
    """Plain-text equivalent for surfaces without a console."""
    lines = [line.plain for line in _headline(snapshot)]
    lines.append("")
    lines.extend(line.plain for line in _bar_chart(snapshot))
    return "\n".join(lines)


def _headline(snapshot: WorkspaceSnapshot) -> list[Text]:
    header = Text()
    header.append("Workspace scan: ", style="dim")
    header.append(snapshot.root)
    cells = (
        ("Git repos found", len(snapshot.repos)),
        (f"Commits last {snapshot.days}d", snapshot.total_commits),
        ("Uncommitted files", snapshot.total_uncommitted),
    )
    columns = Text()
    values = Text()
    for label, value in cells:
        columns.append(f"{label:<{len(label) + 4}}", style="dim")
        values.append(f"{value:<{len(label) + 4}}", style="bold")
    lines = [header, Text(""), columns, values]
    if snapshot.truncated:
        lines.append(Text("Scan stopped at the repository cap; counts are partial.", style="dim"))
    return lines


def _bar_chart(snapshot: WorkspaceSnapshot) -> list[Text]:
    rows = _chart_rows(snapshot)
    title = Text(f"── Activity (commits, last {snapshot.days} days) ──", style="dim")
    if not rows:
        return [title, Text("No commits in this window.", style="dim")]
    label_width = max(len(label) for label, _ in rows)
    peak = max(count for _, count in rows) or 1
    lines = [title]
    for index, (label, count) in enumerate(rows):
        cells = max(1, round(count / peak * _BAR_MAX_CELLS)) if count else 0
        line = Text(f"{label:<{label_width}}  ")
        line.append(_BAR_CELL * cells, style=_SERIES_COLOURS[index % len(_SERIES_COLOURS)])
        line.append(f" {count}", style="dim")
        lines.append(line)
    return lines


def _chart_rows(snapshot: WorkspaceSnapshot) -> list[tuple[str, int]]:
    active = [repo for repo in snapshot.repos if repo.commits > 0]
    rows = [(repo.name, repo.commits) for repo in active[:_TOP_REPOS]]
    rest = sum(repo.commits for repo in active[_TOP_REPOS:])
    if rest:
        rows.append((_OTHERS_LABEL, rest))
    return rows


__all__ = ["render_snapshot", "snapshot_text"]
