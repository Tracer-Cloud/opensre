"""``opensre posthog report`` — PostHog per-metric summary reports (#3824).

Uses the headless posthog-summary skill path (not the
generic ``opensre cron`` kinds). Tasks are stored in the shared scheduler store
but are created and listed only through this command group.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from bootstrap.process import SCHEDULED_COMMAND_PROFILE, configure_process
from infrastructure.scheduling.scheduler.delivery import SUPPORTED_DELIVERY_PROVIDERS
from infrastructure.terminal.theme import GLYPH_ERROR, GLYPH_SUCCESS
from surfaces.cli.commands.scheduling import add_task_and_echo, validate_cron_and_timezone

_console = Console()
_PROVIDER_CHOICES = [p.value for p in SUPPORTED_DELIVERY_PROVIDERS]


@click.group(name="posthog")
def posthog_command() -> None:
    """PostHog-specific automation and reports."""


@posthog_command.group(name="report")
def posthog_report_command() -> None:
    """Run or schedule the PostHog per-metric summary report (#3824)."""


@posthog_report_command.command(name="run")
@click.option(
    "--period",
    "stats_period",
    type=str,
    default="7d",
    show_default=True,
    help="Lookback window for the report (e.g. 24h, 7d, 30d).",
)
@click.option(
    "--metrics",
    "metrics",
    type=str,
    default="",
    help="Optional comma-separated metrics to focus on (default: standard set).",
)
def posthog_report_run(stats_period: str, metrics: str) -> None:
    """Run the metric report once and print it to stdout."""
    from bootstrap.adapters import scheduler_runners
    from integrations.posthog.report_prerequisites import (
        DEFAULT_POSTHOG_PERIOD,
        require_posthog_integration,
    )

    require_posthog_integration()
    configure_process(SCHEDULED_COMMAND_PROFILE)
    payload: dict[str, str] = {
        "source": "cli_posthog_metric_report",
        "stats_period": stats_period.strip() or DEFAULT_POSTHOG_PERIOD,
    }
    if metrics.strip():
        payload["metrics"] = metrics.strip()

    try:
        message = scheduler_runners().agent(payload)
    except Exception as exc:
        _console.print(f"[red]PostHog report failed: {exc}[/red]")
        raise SystemExit(1) from exc

    _console.print(message)


@posthog_report_command.group(name="schedule")
def posthog_report_schedule_command() -> None:
    """Manage scheduled PostHog metric report deliveries."""


@posthog_report_schedule_command.command(name="add")
@click.option(
    "--cron",
    "cron_expr",
    type=str,
    required=True,
    help="Cron expression (5 fields: minute hour day month day_of_week).",
)
@click.option(
    "--tz",
    "timezone",
    type=str,
    default="UTC",
    show_default=True,
    help="IANA timezone for the schedule (e.g. Europe/London, US/Eastern).",
)
@click.option(
    "--provider",
    type=click.Choice(_PROVIDER_CHOICES, case_sensitive=False),
    required=True,
    help="Messaging provider for delivery.",
)
@click.option(
    "--chat-id",
    type=str,
    required=True,
    help="Chat/channel ID for the target provider.",
)
@click.option(
    "--period",
    "stats_period",
    type=str,
    default="7d",
    show_default=True,
    help="Lookback window for the report (e.g. 24h, 7d, 30d).",
)
@click.option(
    "--metrics",
    "metrics",
    type=str,
    default="",
    help="Optional comma-separated metrics to focus on (default: standard set).",
)
def posthog_report_schedule_add(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    stats_period: str,
    metrics: str,
) -> None:
    """Schedule recurring PostHog metric report delivery."""
    from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
    from integrations.posthog.report_prerequisites import (
        DEFAULT_POSTHOG_PERIOD,
        require_posthog_integration,
        require_report_delivery_provider,
    )

    require_posthog_integration()
    require_report_delivery_provider(provider)
    validate_cron_and_timezone(cron_expr, timezone)

    period = stats_period.strip() or DEFAULT_POSTHOG_PERIOD
    params: dict[str, str] = {"stats_period": period}
    if metrics.strip():
        params["metrics"] = metrics.strip()

    task = ScheduledTask(
        kind=TaskKind.POSTHOG_METRIC_REPORT,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        # The report runner reads stats_period from params, not window_hours;
        # keep 0 so operators aren't misled into thinking a lookback-hours
        # window applies here.
        window_hours=0,
        params=params,
    )
    add_task_and_echo(task, label="PostHog report")
    _console.print(f"  Period: {params['stats_period']}")
    if "metrics" in params:
        _console.print(f"  Metrics: {params['metrics']}")


@posthog_report_schedule_command.command(name="list")
def posthog_report_schedule_list() -> None:
    """List scheduled PostHog metric report tasks."""
    from infrastructure.scheduling.scheduler.storage import list_tasks
    from infrastructure.scheduling.scheduler.types import TaskKind

    tasks = [task for task in list_tasks() if task.kind == TaskKind.POSTHOG_METRIC_REPORT]
    if not tasks:
        _console.print("[dim]No PostHog metric report schedules configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Chat")
    table.add_column("Period")
    table.add_column("Enabled")
    table.add_column("Last run")

    for task in tasks:
        period = task.params.get("stats_period", "—")
        table.add_row(
            task.display_id(),
            task.cron,
            task.timezone,
            task.provider.value,
            task.chat_id,
            period or "—",
            GLYPH_SUCCESS if task.enabled else GLYPH_ERROR,
            task.last_run or "—",
        )

    _console.print(table)


@posthog_report_schedule_command.command(name="remove")
@click.argument("task_id")
def posthog_report_schedule_remove(task_id: str) -> None:
    """Remove a scheduled PostHog metric report task."""
    from infrastructure.scheduling.scheduler.storage import get_task, remove_task
    from infrastructure.scheduling.scheduler.types import TaskKind

    task = get_task(task_id)
    if task is None or task.kind != TaskKind.POSTHOG_METRIC_REPORT:
        _console.print(f"[red]Error: PostHog report task {task_id} not found.[/red]")
        raise SystemExit(1)

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@posthog_report_schedule_command.command(name="run")
@click.argument("task_id")
def posthog_report_schedule_run(task_id: str) -> None:
    """Run a scheduled PostHog metric report task immediately."""
    from bootstrap.adapters import scheduler_runners
    from infrastructure.scheduling.scheduler.runner import run_task_now
    from infrastructure.scheduling.scheduler.storage import get_task
    from infrastructure.scheduling.scheduler.types import TaskKind
    from integrations.posthog.report_prerequisites import (
        require_posthog_integration,
        require_report_delivery_provider,
    )

    configure_process(SCHEDULED_COMMAND_PROFILE)
    task = get_task(task_id)
    if task is None or task.kind != TaskKind.POSTHOG_METRIC_REPORT:
        _console.print(f"[red]Error: PostHog report task {task_id} not found.[/red]")
        raise SystemExit(1)

    require_posthog_integration()
    require_report_delivery_provider(task.provider.value)

    _console.print(f"Running PostHog report task {task_id}...")
    success = run_task_now(task_id, scheduler_runners())
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


__all__ = ["posthog_command"]
