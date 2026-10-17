"""Interactive target collection for provider-specific scheduled actions."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from typing import Literal

from config.command_inputs import CommandInput
from core.agent_harness.spi.session_state import set_auto_command
from infrastructure.scheduling.scheduler.types import ScheduledTask
from surfaces.interactive_shell.command_registry.input_collection import can_collect_input
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.scheduled_target import build_scheduled_task_picker
from surfaces.shared.terminal.components.command_input import run_command_input

ScheduledAction = Literal["run", "remove"]


def collect_scheduled_task_target(
    session: Session,
    args: list[str],
    *,
    input_spec: CommandInput,
    load_tasks: Callable[[], Sequence[ScheduledTask] | None],
    slash_command: str,
    action: ScheduledAction,
) -> list[str] | None:
    """Return explicit arguments or collect one visible scheduled-task target."""
    if not can_collect_input(session, input_spec, args):
        return args

    tasks = load_tasks()
    if not tasks:
        return None

    action_label, confirmation, compact_confirmation = {
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
    }[action]
    target = run_command_input(
        build_scheduled_task_picker(
            tasks,
            command=f"{slash_command} {' '.join(args)}",
            action_label=action_label,
            confirmation=confirmation,
            compact_confirmation=compact_confirmation,
        )
    )
    if target is None:
        return None
    if action == "run":
        set_auto_command(session, shlex.join((slash_command, *args, target)))
        return None
    return [*args, target]


__all__ = ["collect_scheduled_task_target"]
