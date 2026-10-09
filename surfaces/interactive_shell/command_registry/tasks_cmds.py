"""Slash commands: /tasks, /cancel, /stop."""

from __future__ import annotations

import re

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from surfaces.interactive_shell.command_registry.types import (
    SlashCommand,
)
from surfaces.interactive_shell.runtime import Session, TaskRecord, TaskStatus
from surfaces.interactive_shell.ui import (
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.descriptions import description_details
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mA-Za-z]")
_MAX_DETAIL_CHARS = 120


def _task_started_label(task: TaskRecord) -> str:
    return format_repl_timestamp(task.started_at, style="utc")


def _task_duration_label(task: TaskRecord) -> str:
    duration = task.duration_seconds()
    if duration is None:
        return "—"
    return f"{duration:.1f}s"


def _clean_first_line(text: str) -> str:
    """Strip ANSI codes and return the first non-empty line of ``text``."""
    clean = _ANSI_ESCAPE.sub("", text)
    return next((line.strip() for line in clean.splitlines() if line.strip()), clean.strip())


def _kind_label(task: TaskRecord) -> str:
    """Return a concise task-kind label."""
    return task.kind.value


def _bounded_first_line(text: str) -> str:
    first_line = _clean_first_line(text)
    if len(first_line) > _MAX_DETAIL_CHARS:
        return first_line[:_MAX_DETAIL_CHARS] + "…"
    return first_line or "—"


def _task_detail_label(task: TaskRecord) -> str:
    if task.status == TaskStatus.RUNNING and task.progress:
        return _bounded_first_line(task.progress)

    # Show error > result > command, first line, truncated.
    return _bounded_first_line(task.error or task.result or task.command or "")


def _cmd_tasks(session: Session, console: Console, _args: list[str]) -> bool:
    tasks = session.task_registry.list_recent(n=50)
    if not tasks:
        console.print(f"[{DIM}]no tasks recorded this session.[/]")
        return True

    rows: list[RecordRow] = []
    status_style = {
        TaskStatus.RUNNING: WARNING,
        TaskStatus.COMPLETED: HIGHLIGHT,
        TaskStatus.CANCELLED: WARNING,
        TaskStatus.FAILED: ERROR,
        TaskStatus.PENDING: DIM,
    }
    for task in tasks:
        st = status_style.get(task.status, DIM)
        title = _bounded_first_line(task.command or _kind_label(task))
        detail = _task_detail_label(task)
        details = [
            Text(f"ID: {task.task_id} · Kind: {_kind_label(task)}", style=DIM),
            Text(f"Started: {_task_started_label(task)}", style=DIM),
        ]
        has_progress = task.status == TaskStatus.RUNNING and bool(task.progress)
        if detail != "—" and (detail != title or task.error or has_progress):
            detail_style = str(ERROR) if task.error else str(WARNING) if has_progress else None
            details.extend(description_details(detail, width=120, style=detail_style))
        rows.append(
            RecordRow(
                (
                    Text(title, style="bold"),
                    Text(task.status.value.capitalize(), style=st),
                    Text(_task_duration_label(task)),
                ),
                tuple(details),
            )
        )
    print_repl_renderable(
        console,
        RecordTable(
            "Tasks",
            (
                RecordColumn("Task"),
                RecordColumn("State", 10),
                RecordColumn("Duration", 10, "right"),
            ),
            tuple(rows),
            caption="Cancel: /cancel <task_id>",
        ),
    )
    return True


def _cmd_stop(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.print(
        f"[{DIM}]in-flight work: press[/] [bold]Ctrl+C[/bold] "
        f"[{DIM}]during a streaming turn, or run[/] [{HIGHLIGHT}]/tasks[/] "
        f"[{DIM}]then[/] [{HIGHLIGHT}]/cancel <id>[/] [{DIM}]for background tasks.[/]"
    )
    return True


def _validate_cancel_args(args: list[str]) -> str | None:
    if not args:
        return f"[{ERROR}]usage:[/] /cancel <task_id>  — use [{HIGHLIGHT}]/tasks[/] to list ids"
    return None


def _cmd_cancel(session: Session, console: Console, args: list[str]) -> bool:
    needle = args[0]
    candidates = session.task_registry.candidates(needle)
    if not candidates:
        console.print(f"[{ERROR}]no task matches id:[/] {escape(needle)}")
        return True
    if len(candidates) > 1:
        console.print(
            f"[{ERROR}]ambiguous id prefix:[/] {escape(needle)} "
            f"[{DIM}]({len(candidates)} matches — use a longer prefix)[/]"
        )
        return True

    task = candidates[0]
    if task.status != TaskStatus.RUNNING:
        console.print(
            f"[{DIM}]task {escape(task.task_id)} already finished (status: {task.status.value}).[/]"
        )
        return True

    task.request_cancel()
    console.print(
        f"[{HIGHLIGHT}]stop requested[/] "
        f"[{DIM}]for {escape(task.kind.value)} {escape(task.task_id)}.[/] "
        f"[{DIM}]use[/] [{HIGHLIGHT}]/tasks[/] [{DIM}]to confirm status.[/]"
    )
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/tasks",
        "List recent and in-flight shell tasks.",
        _cmd_tasks,
        usage=("/tasks",),
    ),
    SlashCommand(
        "/cancel",
        "Cancel a running task by id.",
        _cmd_cancel,
        usage=("/cancel <task_id>",),
        notes=("Use /tasks to list task ids.",),
        validate_args=_validate_cancel_args,
    ),
    SlashCommand(
        "/stop",
        "Show how to stop in-flight turns and background tasks.",
        _cmd_stop,
    ),
]

__all__ = ["COMMANDS"]
