"""Responsive, read-only presentation of scheduled loops."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import TaskRun
from infrastructure.terminal.theme import DIM, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.descriptions import description_details
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def print_schedules(
    console: Console,
    loops: Sequence[LoopSummary],
    latest: Mapping[str, TaskRun],
    *,
    history_available: bool = True,
) -> None:
    """Keep IDs and secondary details outside width-constrained summary columns."""
    rows: list[RecordRow] = []
    for loop in loops:
        state = "Invalid" if loop.schedule_error else "Active" if loop.enabled else "Paused"
        state_style = WARNING if loop.schedule_error else HIGHLIGHT if loop.enabled else DIM
        next_run = (
            format_repl_timestamp(loop.next_run, style="utc").removesuffix(" UTC")
            if loop.enabled and not loop.schedule_error
            else "—"
        )
        channels = ", ".join(loop.channels) or loop.provider.value
        details = [
            Text(f"ID: {loop.id}", style=DIM),
            Text(f"{channels} · TZ: {loop.timezone}", style=DIM),
        ]
        run = latest.get(loop.id)
        if run is None:
            last = (
                f"Last run: {format_repl_timestamp(loop.last_run, style='utc')}"
                if loop.last_run
                else "Not run yet"
            )
            details.append(Text(last if history_available else "History unavailable", style=DIM))
        else:
            delivery = run.delivery_status.value if run.delivery_status is not None else "none"
            style = WARNING if run.error or delivery in {"partial", "failed"} else DIM
            details.append(
                Text(
                    f"Last: {run.status.value} · Work: {run.work_status.value} · Delivery: {delivery}",
                    style=style,
                )
            )
            details.append(
                Text(f"Started: {format_repl_timestamp(run.started_at, style='utc')}", style=DIM)
            )
            if run.work_error_kind:
                details.append(Text(f"Work detail: {run.work_error_kind}", style=WARNING))
        details.extend(description_details(loop.description))
        rows.append(
            RecordRow(
                (
                    Text(loop.name or loop.id, style="bold"),
                    Text(state, style=state_style),
                    Text(loop.cron),
                    Text(next_run),
                ),
                tuple(details),
            )
        )
    schedule_width = max(13, max((cell_len(loop.cron) for loop in loops), default=0))
    console.print(
        RecordTable(
            "Scheduled tasks",
            (
                RecordColumn("Task"),
                RecordColumn("State", 8),
                RecordColumn("Schedule", schedule_width),
                RecordColumn("Next run", 19),
            ),
            tuple(rows),
            subtitle="Run times: UTC",
            caption="History: opensre cron logs <task_id>\nConfiguration: opensre --json cron list",
        )
    )
