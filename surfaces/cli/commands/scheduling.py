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

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.types import ScheduledTask

_console = Console()

#: A standard crontab line: minute, hour, day, month, day_of_week.
CRON_FIELD_COUNT = 5


def validate_cron_and_timezone(cron_expr: str, timezone: str) -> None:
    """Validate cron expression and timezone by constructing an APScheduler trigger.

    Fails fast with a clear error message instead of creating inert tasks.
    """
    parts = cron_expr.split()
    if len(parts) != CRON_FIELD_COUNT:
        _console.print(
            f"[red]Error: cron expression must have exactly {CRON_FIELD_COUNT} fields.[/red]"
        )
        _console.print("  Format: minute hour day month day_of_week")
        _console.print("  Example: 0 9 * * 1-5  (weekdays at 09:00)")
        raise SystemExit(1)

    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(cron_expr, timezone=timezone)
    except (ValueError, TypeError, KeyError) as exc:
        _console.print(f"[red]Error: invalid cron expression or timezone: {exc}[/red]")
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


__all__ = ["CRON_FIELD_COUNT", "add_task_and_echo", "validate_cron_and_timezone"]
