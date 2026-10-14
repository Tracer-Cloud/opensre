"""Exact scheduled-task records for the shared searchable target picker."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from rich.text import Text

from infrastructure.scheduling.scheduler.delivery_plan import configured_target_keys
from infrastructure.scheduling.scheduler.loops import loop_name
from infrastructure.scheduling.scheduler.types import ScheduledTask
from infrastructure.terminal.theme import SECONDARY
from surfaces.interactive_shell.ui.cron_input.presentation import choice_label
from surfaces.shared.terminal.components.target_picker import TargetChoice, build_target_picker
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
        kind = choice_label("kind", task.kind.value)
        targets = configured_target_keys(task)
        channels = ", ".join(
            dict.fromkeys(choice_label("provider", provider.value) for provider, _chat in targets)
        )
        state = "Enabled" if task.enabled else "Disabled"
        summary = f"Kind: {kind} · Channel: {channels} · State: {state}"
        details = (
            f"Schedule: {task.cron}",
            f"TZ: {task.timezone}",
            *(
                f"{choice_label('provider', provider.value)} destination: {chat_id}"
                if len(targets) > 1
                else f"Destination: {chat_id}"
                for provider, chat_id in targets
                if chat_id
            ),
        )
        choices.append(
            TargetChoice(
                value=task.id,
                title=loop_name(task),
                summary=summary,
                details=details,
                compact_details=details,
                search_text=" ".join((task.kind.value, task.provider.value, task.chat_id)),
                cells=tuple(Text(value, style=str(SECONDARY)) for value in (kind, channels, state)),
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
            RecordColumn("Name"),
            RecordColumn("Kind"),
            RecordColumn("Channel"),
            RecordColumn("State"),
        ),
    )
