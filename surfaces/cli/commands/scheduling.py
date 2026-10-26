"""Scheduling helpers shared by the CLI command groups that create tasks.

Holds no Click commands. ``opensre cron``, ``opensre sentry digest``, and
``opensre posthog report`` each accept a cron expression and a timezone, and the
integration-specific groups echo the same confirmation once a task is stored.
Keeping that here means those command modules depend on one helper rather than
importing a private function out of a peer command module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from infrastructure.scheduling.scheduler.cron_expression import (
    CRON_FIELD_COUNT,
    CRON_FIELD_COUNT_ERROR,
    CRON_FIELD_COUNT_WITH_SECONDS,
    CRON_FORMAT_HELP,
    build_cron_trigger,
)

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.types import ScheduledTask

_console = Console()


def validate_cron_and_timezone(cron_expr: str, timezone: str) -> None:
    """Validate cron expression and timezone by constructing an APScheduler trigger.

    Fails fast with a clear error message instead of creating inert tasks.
    """
    if len(cron_expr.split()) not in (CRON_FIELD_COUNT, CRON_FIELD_COUNT_WITH_SECONDS):
        _console.print(f"[red]Error: {CRON_FIELD_COUNT_ERROR}.[/red]")
        _console.print(f"  Format: {CRON_FORMAT_HELP}")
        _console.print("  Example: 0 9 * * mon-fri  (weekdays at 09:00)")
        _console.print("  Example: */30 * * * * *  (every 30 seconds)")
        raise SystemExit(1)

    try:
        build_cron_trigger(cron_expr, timezone)
    except ValueError as exc:
        _console.print(f"[red]Error: {exc}[/red]")
        raise SystemExit(1) from exc


def add_task_and_echo(task: ScheduledTask, *, label: str) -> ScheduledTask:
    """Store ``task`` and report the schedule and destination it was created with.

    ``label`` names the kind of task in the confirmation line (for example
    ``"Sentry digest"``). Returns the stored task, whose ``id`` may differ from
    the requested one when the store deduplicates an equivalent schedule.
    """
    from infrastructure.scheduling.scheduler.storage import add_task

    added = add_task(task)
    _console.print(f"[green]{label} task {added.id} created.[/green]")
    _console.print(f"  Cron: {added.cron}  TZ: {added.timezone}")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")
    return added


__all__ = ["add_task_and_echo", "validate_cron_and_timezone"]
