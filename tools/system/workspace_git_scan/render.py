"""Terminal rendering of a workspace snapshot: headline counts and a commit bar chart."""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from infrastructure.terminal.theme import BRAND, ERROR, HIGHLIGHT, SECONDARY, WARNING
from tools.system.workspace_git_scan.scan import RepoActivity, WorkspaceSnapshot

_TOP_REPOS = 4
_BAR_MAX_CELLS = 40
_BAR_CELL = "█"
_SERIES_STYLES = (HIGHLIGHT, BRAND, SECONDARY, WARNING, ERROR)
_OTHERS_LABEL = "all others"


def snapshot_renderable(snapshot: WorkspaceSnapshot) -> Group:
    """Headline counts and the activity bar chart as one Rich renderable."""
    return Group(*_headline(snapshot), Text(""), *_bar_chart(snapshot))


def render_snapshot(console: Any, snapshot: WorkspaceSnapshot) -> None:
    """Print the headline counts and the activity bar chart to *console*."""
    console.print(snapshot_renderable(snapshot))


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
    total = snapshot.total_commits or 1
    lines = [title]
    for index, (label, count) in enumerate(rows):
        cells = max(1, round(count / peak * _BAR_MAX_CELLS)) if count else 0
        line = Text(f"{label:<{label_width}}  ")
        line.append(_BAR_CELL * cells, style=_SERIES_STYLES[index % len(_SERIES_STYLES)])
        line.append(f" {count} ({count / total:.0%})", style="dim")
        lines.append(line)
    return lines


def _chart_rows(snapshot: WorkspaceSnapshot) -> list[tuple[str, int]]:
    active = [repo for repo in snapshot.repos if repo.commits > 0]
    top = active[:_TOP_REPOS]
    labels = [_label(repo) for repo in top]
    rows = [
        (
            repo.github_full_name if labels.count(label) > 1 and repo.github_full_name else label,
            repo.commits,
        )
        for repo, label in zip(top, labels, strict=True)
    ]
    rest = sum(repo.commits for repo in active[_TOP_REPOS:])
    if rest:
        rows.append((_OTHERS_LABEL, rest))
    return rows


def _label(repo: RepoActivity) -> str:
    return repo.github_repo or repo.name


__all__ = ["render_snapshot", "snapshot_renderable", "snapshot_text"]
