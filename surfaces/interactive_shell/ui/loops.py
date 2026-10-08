"""Compact loop results and full report presentation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, tzinfo

from rich.console import Console
from rich.text import Text

from infrastructure.scheduling.scheduler.loop_constants import LOOP_MODE_REPORT
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus
from infrastructure.terminal.markdown import ReplyMarkdown, UnpaddedRows
from infrastructure.terminal.theme import BOLD_BRAND, DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.rendering import (
    print_repl_renderable,
    print_repl_table,
    repl_table,
)
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


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
    if run.work_outcome.completed and run.targets and any(not target.ok for target in run.targets):
        return "Delivery issue", WARNING
    if run.work_status.value in {"blocked", "incomplete"}:
        return run.work_status.value.capitalize(), WARNING
    if run.work_status.value == "failed":
        return "Work failed", ERROR
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
    """Show schedule records with purpose, latest findings and full-report navigation."""
    timestamp = now or datetime.now(UTC)
    rows: list[RecordRow] = []
    for loop in loops:
        state = "Invalid" if loop.schedule_error else "Active" if loop.enabled else "Paused"
        state_style = WARNING if loop.schedule_error else HIGHLIGHT if loop.enabled else DIM
        details = [
            Text(f"ID: {loop.id}", style=DIM),
            Text(f"{', '.join(loop.channels)} · TZ: {loop.timezone}", style=DIM),
        ]
        if loop.description:
            details.append(Text(f"What it does: {loop.description}", style=DIM))
        run = latest.get(loop.id)
        if run is None:
            details.append(
                Text("History unavailable" if loop.last_run else "Not run yet", style=DIM)
            )
        else:
            status, style = _status(run)
            details.append(
                Text(
                    f"Latest result: {status} · {_age(run.finished_at or run.started_at, timestamp)}",
                    style=style,
                )
            )
            details.append(_clipped(_finding(run), 160))
        if loop.schedule_error:
            details.append(Text(f"Requires action: {loop.schedule_error}", style=WARNING))
        rows.append(
            RecordRow(
                (
                    Text(loop.name, style="bold"),
                    Text(state, style=state_style),
                    Text(loop.cron),
                    Text(_next_run(loop, timestamp, local_timezone), style=DIM),
                ),
                tuple(details),
            )
        )
    print_repl_renderable(
        console,
        RecordTable(
            "Loops",
            (
                RecordColumn("Loop"),
                RecordColumn("State", 8),
                RecordColumn("Schedule", 17),
                RecordColumn("Next run", 14),
            ),
            tuple(rows),
            subtitle="Next run: local time",
            caption="/loops show <name-or-id> — full reports and configuration",
        ),
    )


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
        console.print(Text(f"Work: {selected.work_status.value}"))
        if selected.work_error_kind:
            console.print(Text(selected.work_error_kind, style=ERROR))
        delivery = selected.delivery_status
        console.print(Text(f"Delivery: {delivery.value if delivery is not None else 'none'}"))
        console.print(Text(f"Started: {_exact_time(selected.started_at)}", style=DIM))
        if selected.finished_at:
            console.print(Text(f"Finished: {_exact_time(selected.finished_at)}", style=DIM))
        if selected.report and selected.report.strip():
            print_repl_renderable(console, UnpaddedRows(ReplyMarkdown(selected.report)))
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
