"""Record adapters for loop and durable-work target selection."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application

from core.domain.work_items import WorkItem
from infrastructure.scheduling.scheduler.loops import LoopSummary
from surfaces.shared.terminal.components.target_picker import TargetChoice, build_target_picker
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.descriptions import description_details


def _preview(description: str) -> tuple[str, ...]:
    lines = description_details(description)
    return (lines[-1].plain,) if lines else ()


def build_loop_picker(
    loops: Sequence[LoopSummary],
    *,
    command: str,
    action_label: str,
    confirmation: str = "",
    compact_confirmation: str = "",
) -> Application[str | None]:
    """Show loop identity, state, schedule and searchable descriptions."""
    choices = tuple(
        TargetChoice(
            value=loop.id,
            title=loop.name,
            summary=" · ".join(
                (
                    "Invalid" if loop.schedule_error else loop.status.capitalize(),
                    f"Cron {loop.cron}",
                    f"TZ {loop.timezone}",
                    f"Mode {loop.mode}",
                    ", ".join(channel.replace("_", " ") for channel in loop.channels),
                )
            ),
            details=(
                *_preview(loop.description or loop.prompt),
                f"Full details: /loops show {loop.id}",
            ),
            search_text=" ".join((loop.description, loop.prompt, loop.cron, *loop.task_ids)),
            summary_style="error" if loop.schedule_error else "hint",
            compact_summary=f"{'Invalid' if loop.schedule_error else loop.mode.capitalize()} · TZ {loop.timezone}",
        )
        for loop in loops
    )
    return build_target_picker(
        choices,
        title=command,
        heading=f"Choose a loop · {action_label}",
        action_label=action_label,
        confirmation=confirmation,
        compact_confirmation=compact_confirmation,
    )


def build_work_picker(
    items: Sequence[WorkItem],
    *,
    command: str,
    action_label: str,
    confirmation: str = "",
) -> Application[str | None]:
    """Show durable work titles, stable IDs and relevant project context."""
    choices = tuple(
        TargetChoice(
            value=item.id,
            title=item.title,
            summary=" · ".join(
                value
                for value in (
                    item.status.value.capitalize(),
                    item.priority.value.capitalize(),
                    f"Project {item.project}" if item.project else "",
                )
                if value
            ),
            details=tuple(
                value
                for value in (
                    f"Owner: {item.owner}" if item.owner else "",
                    f"Due: {format_repl_timestamp(item.due_at, style='utc')}"
                    if item.due_at
                    else "",
                )
                if value
            ),
            search_text=item.notes,
            compact_summary=f"{item.status.value.capitalize()} · {item.priority.value.capitalize()}",
        )
        for item in items
    )
    return build_target_picker(
        choices,
        title=command,
        heading=f"Choose a work item · {action_label}",
        action_label=action_label,
        confirmation=confirmation,
    )
