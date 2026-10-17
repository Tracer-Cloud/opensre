"""Record presentation for provider-specific scheduled-task target selection."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from rich.text import Text

from infrastructure.scheduling.scheduler.types import ScheduledTask
from infrastructure.terminal.theme import DIM, HIGHLIGHT, SECONDARY, WARNING
from surfaces.shared.terminal.components.target_picker import TargetChoice, build_target_picker
from surfaces.shared.terminal.tables.records import RecordColumn


def _task_title(task: ScheduledTask) -> str:
    """Return a useful picker title when the persisted task has no custom name."""
    return task.name.strip() or task.kind.value.replace("_", " ").title()


def _task_details(task: ScheduledTask) -> tuple[str, ...]:
    details = [
        f"Destination: {task.provider.value}:{task.chat_id or 'default'}",
        f"Schedule: {task.cron} · TZ {task.timezone}",
    ]
    for key, label in (
        ("project_slug", "Project"),
        ("stats_period", "Period"),
        ("metrics", "Metrics"),
    ):
        value = task.params.get(key, "").strip()
        if value:
            details.append(f"{label}: {value}")
    return tuple(details)


def build_scheduled_task_picker(
    tasks: Sequence[ScheduledTask],
    *,
    command: str,
    action_label: str,
    confirmation: str = "",
    compact_confirmation: str = "",
) -> Application[str | None]:
    """Show scheduled-task identity, delivery, and schedule details in a picker."""
    choices = tuple(
        TargetChoice(
            value=task.id,
            title=_task_title(task),
            summary=" · ".join(
                (
                    "Active" if task.enabled else "Paused",
                    f"{task.provider.value}:{task.chat_id or 'default'}",
                    task.cron,
                    f"TZ {task.timezone}",
                )
            ),
            details=_task_details(task),
            search_text=" ".join(
                (
                    task.id,
                    task.name,
                    task.kind.value,
                    task.provider.value,
                    task.chat_id,
                    task.cron,
                    task.timezone,
                    *task.params.values(),
                )
            ),
            summary_style="description" if task.enabled else "warning",
            compact_summary=(
                f"{task.provider.value}:{task.chat_id or 'default'} · "
                f"{task.cron} · TZ {task.timezone}"
            ),
            cells=(
                Text(
                    f"{task.provider.value}:{task.chat_id or 'default'}",
                    style=str(SECONDARY),
                ),
                Text(task.cron, style=str(SECONDARY)),
                Text(task.timezone, style=str(SECONDARY)),
                Text(
                    "Active" if task.enabled else "Paused",
                    style=str(HIGHLIGHT if task.enabled else WARNING),
                ),
            ),
            metadata=(Text(f"Full task ID: {task.id}", style=str(DIM)),),
        )
        for task in tasks
    )
    return build_target_picker(
        choices,
        title=command,
        heading="Scheduled task",
        action_label=action_label,
        confirmation=confirmation,
        compact_confirmation=compact_confirmation,
        columns=(
            RecordColumn("Task"),
            RecordColumn("Destination"),
            RecordColumn("Schedule"),
            RecordColumn("TZ"),
            RecordColumn("State"),
        ),
        fit_columns=True,
    )


__all__ = ["build_scheduled_task_picker"]
