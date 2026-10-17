"""Interactive target selection for PostHog scheduled actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from rich.console import Console

from config.command_inputs import (
    POSTHOG_REPORT_SCHEDULE_REMOVE_INPUT,
    POSTHOG_REPORT_SCHEDULE_RUN_INPUT,
    CommandInput,
)
from infrastructure.scheduling.scheduler.storage.task_store import get_task_store_snapshot
from infrastructure.scheduling.scheduler.task_visibility import visible_to_current_scope
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskKind
from infrastructure.terminal.theme import DIM, WARNING
from surfaces.interactive_shell.command_registry.scheduled_targets import (
    collect_scheduled_task_target,
)
from surfaces.interactive_shell.runtime import Session

_TARGET_COMMANDS: tuple[tuple[CommandInput, Literal["run", "remove"], str], ...] = (
    (
        POSTHOG_REPORT_SCHEDULE_RUN_INPUT,
        "run",
        "No PostHog metric report schedules configured.",
    ),
    (
        POSTHOG_REPORT_SCHEDULE_REMOVE_INPUT,
        "remove",
        "No PostHog metric report schedules configured.",
    ),
)


def _load_posthog_tasks(
    console: Console,
    *,
    empty_message: str,
) -> tuple[ScheduledTask, ...] | None:
    """Load the visible PostHog report schedules."""
    snapshot = get_task_store_snapshot(read_only=True)
    if not snapshot.complete:
        console.print(
            f"[{WARNING}]Could not read all scheduled tasks. Check /cron status --json, "
            "repair the scheduler task store, then retry.[/]"
        )
        return None
    tasks = tuple(
        task
        for task in snapshot.tasks
        if task.kind == TaskKind.POSTHOG_METRIC_REPORT and visible_to_current_scope(task)
    )
    if not tasks:
        console.print(f"[{DIM}]{empty_message}[/]")
        return None
    return tasks


def _collect_target(session: Session, console: Console, args: list[str]) -> list[str] | None:
    for input_spec, action, empty_message in _TARGET_COMMANDS:
        if input_spec.matches(input_spec.command, args):

            def load_tasks(message: str = empty_message) -> tuple[ScheduledTask, ...] | None:
                return _load_posthog_tasks(console, empty_message=message)

            return collect_scheduled_task_target(
                session,
                args,
                input_spec=input_spec,
                load_tasks=load_tasks,
                slash_command="/posthog",
                action=action,
            )
    return args


def cmd_posthog(
    session: Session,
    console: Console,
    args: list[str],
    *,
    run_cli: Callable[..., bool],
) -> bool:
    """Collect a missing scheduled-task target before delegating to the CLI."""
    collected = _collect_target(session, console, args)
    if collected is None:
        return True
    return run_cli(console, ["posthog", *collected], capture_output=True, session=session)


__all__ = ["cmd_posthog"]
