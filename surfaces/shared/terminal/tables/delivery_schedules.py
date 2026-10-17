"""Responsive provider-specific delivery schedule lists for CLI and REPL."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from config.constants.delivery_schedule_lists import DELIVERY_SCHEDULE_LISTS
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.terminal.theme import DIM, HIGHLIGHT
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def print_delivery_schedule_list(console: Console, path: tuple[str, ...]) -> None:
    """Render one delivery-task kind without sacrificing full IDs or details."""
    spec = DELIVERY_SCHEDULE_LISTS[path]
    tasks = [task for task in list_tasks() if task.kind.value == spec.kind]
    if not tasks:
        console.print(Text(spec.empty_message, style=DIM))
        return

    show_chat = any(task.chat_id.strip() for task in tasks)
    show_detail = any(task.params.get(spec.detail_key, "").strip() for task in tasks)
    rows: list[RecordRow] = []
    for task in tasks:
        detail = task.params.get(spec.detail_key, "")
        cells = [
            Text(task.name or task.id, style="bold"),
            Text(task.provider.value),
        ]
        if show_chat:
            cells.append(Text(task.chat_id))
        if show_detail:
            cells.append(Text(detail))
        cells.extend(
            (
                Text(
                    "Active" if task.enabled else "Paused",
                    style=HIGHLIGHT if task.enabled else DIM,
                ),
                Text(task.cron),
                Text(task.timezone),
                Text(format_repl_timestamp(task.last_run, style="utc") if task.last_run else "—"),
            )
        )

        rows.append(
            RecordRow(
                tuple(cells),
                metadata=(Text(f"ID: {task.id}", style=DIM),),
            )
        )

    run_path = " ".join((*path[:-1], "run"))
    remove_path = " ".join((*path[:-1], "remove"))
    print_repl_renderable(
        console,
        RecordTable(
            spec.title,
            (
                RecordColumn("Task"),
                RecordColumn("Provider"),
                *((RecordColumn("Chat"),) if show_chat else ()),
                *((RecordColumn(spec.detail_label),) if show_detail else ()),
                RecordColumn("State"),
                RecordColumn("Schedule"),
                RecordColumn("TZ"),
                RecordColumn("Last run"),
            ),
            tuple(rows),
            subtitle="Run times: UTC",
            caption=(
                f"Run: opensre {run_path} <task_id>\n"
                f"Remove: opensre {remove_path} <task_id>\n"
                "History: opensre cron logs <task_id>"
            ),
        ),
    )


__all__ = ["print_delivery_schedule_list"]
