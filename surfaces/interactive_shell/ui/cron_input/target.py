"""Exact scheduled-task records for the shared searchable target picker."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from rich.text import Text

from infrastructure.scheduling.scheduler.delivery_plan import configured_target_keys
from infrastructure.scheduling.scheduler.loops import loop_name, summarize_loop
from infrastructure.scheduling.scheduler.types import ScheduledTask
from infrastructure.terminal.theme import DIM, HIGHLIGHT, SECONDARY, WARNING
from surfaces.shared.terminal.components.target_picker import TargetChoice, build_target_picker
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.records import RecordColumn


def build_cron_target_picker(
    tasks: Sequence[ScheduledTask], *, subcommand: str
) -> Application[str | None]:
    """Present each task independently, including tasks belonging to the same loop."""
    action, confirmation, compact_confirmation = {
        "logs": ("Show logs", "", ""),
        "run": (
            "Run now",
            "Run this scheduled task now with its configured delivery?",
            "Run with configured delivery?",
        ),
        "remove": (
            "Remove permanently",
            "Permanently remove this scheduled task? This cannot be undone. Logs are kept.",
            "Cannot be undone. Logs are kept.",
        ),
    }[subcommand]
    choices: list[TargetChoice] = []
    for task in tasks:
        targets = configured_target_keys(task)
        channels = ", ".join(
            dict.fromkeys(
                "local" if provider.value == "interactive_shell" else provider.value
                for provider, _chat in targets
            )
        )
        loop = summarize_loop(task)
        state = "Invalid" if loop.schedule_error else "Active" if task.enabled else "Paused"
        state_style = WARNING if loop.schedule_error else HIGHLIGHT if task.enabled else DIM
        next_run = (
            format_repl_timestamp(loop.next_run, style="utc").removesuffix(" UTC")
            if task.enabled and not loop.schedule_error
            else "—"
        )
        search_text = " ".join(
            (
                task.kind.value,
                *(provider.value for provider, _chat_id in targets),
                *(chat_id for _provider, chat_id in targets),
            )
        )
        choices.append(
            TargetChoice(
                value=task.id,
                title=loop_name(task),
                search_text=search_text,
                cells=(
                    Text(channels, style=str(SECONDARY)),
                    Text(state, style=str(state_style)),
                    Text(task.cron, style=str(SECONDARY)),
                    Text(task.timezone, style=str(SECONDARY)),
                    Text(next_run, style=str(SECONDARY)),
                ),
            )
        )
    return build_target_picker(
        choices,
        title=f"/cron {subcommand}",
        heading="Scheduled task",
        action_label=action,
        confirmation=confirmation,
        compact_confirmation=compact_confirmation,
        columns=(
            RecordColumn("Task"),
            RecordColumn("Channel"),
            RecordColumn("State"),
            RecordColumn("Schedule"),
            RecordColumn("TZ"),
            RecordColumn("Next run"),
        ),
        fit_columns=True,
    )
