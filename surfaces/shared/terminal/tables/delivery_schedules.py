"""Responsive delivery schedule lists shared by CLI and REPL."""

from __future__ import annotations

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from config.constants.delivery_schedule_lists import DELIVERY_SCHEDULE_LISTS
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.terminal.theme import DIM, HIGHLIGHT
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def print_delivery_schedule_list(console: Console, path: tuple[str, ...]) -> None:
    """Load one task kind and retain full identifiers and destination context."""
    spec = DELIVERY_SCHEDULE_LISTS[path]
    tasks = [task for task in list_tasks() if task.kind.value == spec.kind]
    if not tasks:
        console.print(Text(spec.empty_message, style=DIM))
        return
    rows: list[RecordRow] = []
    for task in tasks:
        details = [
            Text(f"ID: {task.id}", style=DIM),
            Text(f"{task.provider.value} · TZ: {task.timezone}", style=DIM),
        ]
        if task.chat_id:
            details.append(Text(f"Chat: {task.chat_id}", style=DIM))
        value = task.params.get(spec.detail_key, "")
        if value:
            details.append(Text(f"{spec.detail_label}: {value}", style=DIM))
        rows.append(
            RecordRow(
                (
                    Text(task.name or task.id, style="bold"),
                    Text(
                        "Active" if task.enabled else "Paused",
                        style=HIGHLIGHT if task.enabled else DIM,
                    ),
                    Text(task.cron),
                    Text(
                        format_repl_timestamp(task.last_run, style="utc")
                        if task.last_run
                        else "Not run yet"
                    ),
                ),
                tuple(details),
            )
        )
    print_repl_renderable(
        console,
        RecordTable(
            spec.title,
            (
                RecordColumn("Task"),
                RecordColumn("State", 8),
                RecordColumn("Schedule", max(13, max(cell_len(task.cron) for task in tasks))),
                RecordColumn("Last run", 23),
            ),
            tuple(rows),
            subtitle="Run times: UTC",
            caption=f"Run: opensre {' '.join((*path[:-1], 'run'))} <task_id>\nHistory: opensre cron logs <task_id>",
        ),
    )


__all__ = ["print_delivery_schedule_list"]
