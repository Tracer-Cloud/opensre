"""Compact loop results and full report presentation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, tzinfo

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_REPORT
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus
from infrastructure.terminal.theme import BOLD_BRAND, DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.rendering import (
    print_repl_renderable,
    print_repl_table,
    repl_output_width,
    repl_table,
)


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _age(value: str, now: datetime) -> str:
    timestamp = _timestamp(value)
    if timestamp is None:
        return "Time unknown"
    seconds = max(0, int((now - timestamp).total_seconds()))
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _next_run(loop: LoopSummary, now: datetime, local_timezone: tzinfo | None) -> str:
    if not loop.enabled:
        return "Paused"
    if loop.schedule_error:
        return "Invalid schedule"
    timestamp = _timestamp(loop.next_run)
    if timestamp is None:
        return "Not scheduled"
    local = timestamp.astimezone(local_timezone)
    days = (local.date() - now.astimezone(local_timezone).date()).days
    if days == 0:
        day = "Today"
    elif days == 1:
        day = "Tomorrow"
    elif 1 < days < 7:
        day = local.strftime("%a")
    else:
        day = local.strftime("%d %b")
    return f"{day} {local:%H:%M}"


def _status(run: TaskRun) -> tuple[str, str]:
    if run.status is TaskStatus.SUCCESS:
        if run.error or any(not target.ok for target in run.targets):
            return "Delivery issue", WARNING
        return "Done", HIGHLIGHT
    return {
        TaskStatus.PENDING: ("Queued", DIM),
        TaskStatus.RUNNING: ("Running", HIGHLIGHT),
        TaskStatus.FAILED: ("Failed", ERROR),
        TaskStatus.ABANDONED: ("Interrupted", WARNING),
        TaskStatus.SKIPPED: ("Skipped", DIM),
    }[run.status]


def _excerpt(report: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    # Prefer report content over Markdown section headings; preserve a heading-only report.
    line = next((line for line in lines if not line.startswith("#")), lines[0] if lines else "")
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    return " ".join(line.lstrip("#>- ").replace("**", "").replace("`", "").split())


def _finding(run: TaskRun) -> str:
    if run.report_summary.strip():
        return _excerpt(run.report_summary)
    if run.report is not None:
        return _excerpt(run.report) or "No report"
    if run.status is TaskStatus.PENDING:
        return "Awaiting execution"
    if run.status is TaskStatus.RUNNING:
        return "Report in progress"
    if run.status in {TaskStatus.FAILED, TaskStatus.ABANDONED}:
        return "Report unavailable"
    return "Report not retained"


def _clipped(value: str, width: int, *, style: str = "") -> Text:
    text = Text(" ".join(value.split()), style=style)
    text.truncate(width, overflow="ellipsis")
    return text


def render_loops(
    console: Console,
    loops: Sequence[LoopSummary],
    latest: Mapping[str, TaskRun],
    *,
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> None:
    """Show at most two lines per loop, giving findings the largest column."""
    timestamp = now or datetime.now(UTC)
    width = repl_output_width(console)
    next_width = 14 if width >= 70 else 11
    name_width = max(9, min(34, (width - next_width - 6) // 3))
    result_width = width - next_width - name_width - 6
    table = repl_table(title="Loops\n", title_style=BOLD_BRAND)
    table.add_column("Loop", width=name_width, no_wrap=True)
    table.add_column("Latest result", width=result_width, no_wrap=True)
    table.add_column("Next run", width=next_width, no_wrap=True, style=DIM)
    for loop in loops:
        name, separator, context = loop.name.partition(" · ")
        label = _clipped(name, name_width, style="bold")
        if separator:
            label.append("\n")
            label.append_text(_clipped(context, name_width, style=DIM))
        run = latest.get(loop.id)
        if run is None:
            result = Text("History unavailable" if loop.last_run else "Not run yet", style=DIM)
        else:
            status, style = _status(run)
            result = _clipped(
                f"{status} · {_age(run.finished_at or run.started_at, timestamp)}",
                result_width,
                style=style,
            )
            result.append("\n")
            result.append_text(_clipped(_finding(run), result_width))
        table.add_row(
            label,
            result,
            _clipped(_next_run(loop, timestamp, local_timezone), next_width),
        )
    print_repl_table(console, table, width=width)
    console.print(Text("/loops show — full reports and configuration", style=DIM))


def _exact_time(value: str | None) -> str:
    timestamp = _timestamp(value)
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z (%z)") if timestamp else "—"


def render_loop_details(
    console: Console,
    loop: LoopSummary,
    runs: Sequence[TaskRun],
    selected: TaskRun | None,
) -> None:
    """Present one full report, recent run history, then loop configuration."""
    console.print(Text(loop.name, style=BOLD_BRAND))
    if selected is None:
        console.print(Text("History unavailable" if loop.last_run else "Not run yet", style=DIM))
    else:
        status, style = _status(selected)
        console.print(Text(f"Run {selected.run_id} · {status}", style=style))
        console.print(Text(f"Started: {_exact_time(selected.started_at)}", style=DIM))
        if selected.finished_at:
            console.print(Text(f"Finished: {_exact_time(selected.finished_at)}", style=DIM))
        if selected.report and selected.report.strip():
            print_repl_renderable(console, Markdown(selected.report))
        else:
            console.print(Text(_finding(selected), style=DIM))
        if selected.error:
            console.print(Text(selected.error, style=ERROR))

    if runs:
        table = repl_table(title="Recent runs", title_style=BOLD_BRAND)
        table.add_column("Run", no_wrap=True)
        table.add_column("Started", style=DIM, no_wrap=True)
        table.add_column("Result", overflow="fold")
        for run in runs:
            status, style = _status(run)
            result = Text(f"{status} · {_finding(run)}", style=style)
            result.truncate(100, overflow="ellipsis")
            table.add_row(str(run.run_id), _exact_time(run.started_at), result)
        print_repl_table(console, table)
        console.print(
            Text(f"/loops show {loop.id} --run <Run> — open an earlier report", style=DIM)
        )

    console.print(Text("Configuration", style=BOLD_BRAND))
    rows = (
        ("ID", loop.id),
        ("Status", "Active" if loop.enabled else "Paused"),
        ("Schedule", f"{loop.cron} ({loop.timezone})"),
        ("Next run", _exact_time(loop.next_run) if loop.enabled else "Paused"),
        ("Channels", ", ".join(channel.replace("_", " ") for channel in loop.channels)),
        ("Mode", loop.mode if loop.mode != LOOP_MODE_REPORT else ""),
        ("Prompt", loop.prompt),
        ("Schedule error", loop.schedule_error),
    )
    for key, value in rows:
        if value:
            console.print(Text(f"{key}: {value}"))


__all__ = ["render_loop_details", "render_loops"]
