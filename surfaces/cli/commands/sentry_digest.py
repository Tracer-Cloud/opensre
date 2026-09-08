"""``opensre sentry digest`` — scheduled Sentry morning digest delivery.

Uses the headless sentry-summary skill path (not the
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


@click.group(name="sentry")
def sentry_command() -> None:
    """Sentry-specific automation and digests."""


@sentry_command.group(name="digest")
def sentry_digest_command() -> None:
    """Run or schedule the Sentry morning digest (#10)."""


@sentry_command.group(name="uptime")
def sentry_uptime_command() -> None:
    """Poll Sentry uptime monitors and notify on downtime transitions (#4032)."""


@sentry_uptime_command.command(name="check")
@click.option(
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the listing.",
)
def sentry_uptime_check(project_slug: str) -> None:
    """List current Sentry uptime monitor health (no notification)."""
    from integrations.sentry.uptime import list_sentry_uptime_monitors, resolve_sentry_config

    config = resolve_sentry_config(project_slug=project_slug)
    if config is None:
        _console.print(
            "[red]Sentry is not configured.[/red] Run `opensre integrations setup` and verify "
            "with `opensre integrations verify sentry`."
        )
        raise SystemExit(1)

    try:
        monitors = list_sentry_uptime_monitors(config=config)
    except Exception as exc:
        _console.print(f"[red]Sentry uptime check failed: {exc}[/red]")
        raise SystemExit(1) from exc

    if not monitors:
        _console.print("[dim]No Sentry uptime monitors found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Health")
    table.add_column("Name")
    table.add_column("URL")
    table.add_column("Project")
    for monitor in monitors:
        health_style = "red" if monitor.health == "down" else "green"
        table.add_row(
            f"[{health_style}]{monitor.health}[/{health_style}]",
            monitor.name,
            monitor.url or "—",
            monitor.project_slug or "—",
        )
    _console.print(table)


@sentry_uptime_command.group(name="watch")
def sentry_uptime_watch_command() -> None:
    """Schedule polling that pings Slack/Telegram/Rocket.Chat on uptime transitions."""


@sentry_uptime_watch_command.command(name="add")
@click.option(
    "--cron",
    "cron_expr",
    type=str,
    default="*/5 * * * *",
    show_default=True,
    help="Cron expression (5 fields). Prefer a short interval for downtime pings.",
)
@click.option(
    "--tz",
    "timezone",
    type=str,
    default="UTC",
    show_default=True,
    help="IANA timezone for the schedule.",
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
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the watch.",
)
def sentry_uptime_watch_add(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    project_slug: str,
) -> None:
    """Schedule Sentry uptime watch delivery (notify only on transitions)."""
    from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
    from integrations.sentry.digest_prerequisites import (
        require_digest_delivery_provider,
        require_sentry_integration,
    )

    require_sentry_integration()
    require_digest_delivery_provider(provider)
    validate_cron_and_timezone(cron_expr, timezone)

    params: dict[str, str] = {}
    if project_slug.strip():
        params["project_slug"] = project_slug.strip()

    task = ScheduledTask(
        kind=TaskKind.SENTRY_UPTIME_WATCH,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        # Unused by the uptime watcher (poll is point-in-time); keep 0 so
        # operators aren't misled into thinking a lookback window applies.
        window_hours=0,
        params=params,
    )
    added = add_task_and_echo(task, label="Sentry uptime watch")
    if params:
        _console.print(f"  Project: {params['project_slug']}")

    from bootstrap.adapters import install_scheduled_delivery_adapters
    from infrastructure.scheduling.scheduler.executor import deliver_scheduled_message
    from integrations.sentry.uptime import format_uptime_watch_active_message

    active_message = format_uptime_watch_active_message(
        task_id=added.id,
        cron=added.cron,
        timezone=added.timezone,
        project_slug=params.get("project_slug", ""),
    )
    # This command boots no scheduler profile, so bind the delivery adapters the
    # activation notice resolves through before sending it.
    install_scheduled_delivery_adapters()
    ok, error, _message_id = deliver_scheduled_message(added, active_message)
    if ok:
        _console.print("[green]Activation notice sent to chat.[/green]")
    else:
        _console.print(f"[yellow]Watch scheduled, but activation notice failed: {error}[/yellow]")


@sentry_uptime_watch_command.command(name="list")
def sentry_uptime_watch_list() -> None:
    """List scheduled Sentry uptime watch tasks."""
    from infrastructure.scheduling.scheduler.storage import list_tasks
    from infrastructure.scheduling.scheduler.types import TaskKind

    tasks = [task for task in list_tasks() if task.kind == TaskKind.SENTRY_UPTIME_WATCH]
    if not tasks:
        _console.print("[dim]No Sentry uptime watch schedules configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Chat")
    table.add_column("Project")
    table.add_column("Enabled")
    table.add_column("Last run")

    for task in tasks:
        project = task.params.get("project_slug", "—")
        table.add_row(
            task.display_id(),
            task.cron,
            task.timezone,
            task.provider.value,
            task.chat_id,
            project or "—",
            GLYPH_SUCCESS if task.enabled else GLYPH_ERROR,
            task.last_run or "—",
        )
    _console.print(table)


@sentry_uptime_watch_command.command(name="remove")
@click.argument("task_id")
def sentry_uptime_watch_remove(task_id: str) -> None:
    """Remove a scheduled Sentry uptime watch task."""
    from infrastructure.scheduling.scheduler.storage import get_task, remove_task
    from infrastructure.scheduling.scheduler.types import TaskKind

    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_UPTIME_WATCH:
        _console.print(f"[red]Error: Sentry uptime watch task {task_id} not found.[/red]")
        raise SystemExit(1)

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@sentry_uptime_watch_command.command(name="run")
@click.argument("task_id")
def sentry_uptime_watch_run(task_id: str) -> None:
    """Run a scheduled Sentry uptime watch task immediately."""
    from bootstrap.adapters import scheduler_runners
    from infrastructure.scheduling.scheduler.runner import run_task_now
    from infrastructure.scheduling.scheduler.storage import get_task
    from infrastructure.scheduling.scheduler.types import TaskKind
    from integrations.sentry.digest_prerequisites import require_digest_delivery_provider

    configure_process(SCHEDULED_COMMAND_PROFILE)
    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_UPTIME_WATCH:
        _console.print(f"[red]Error: Sentry uptime watch task {task_id} not found.[/red]")
        raise SystemExit(1)

    require_digest_delivery_provider(task.provider.value)

    _console.print(f"Running Sentry uptime watch task {task_id}...")
    success = run_task_now(task_id, scheduler_runners())
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


@sentry_digest_command.command(name="run")
@click.option(
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the digest.",
)
def sentry_digest_run(project_slug: str) -> None:
    """Run the morning digest once and print the report to stdout."""
    from bootstrap.adapters import scheduler_runners

    configure_process(SCHEDULED_COMMAND_PROFILE)
    payload: dict[str, str] = {
        "source": "cli_sentry_morning_digest",
        "stats_period": "24h",
        "query": "is:unresolved",
    }
    if project_slug.strip():
        payload["project_slug"] = project_slug.strip()

    try:
        message = scheduler_runners().agent(payload)
    except Exception as exc:
        _console.print(f"[red]Sentry morning digest failed: {exc}[/red]")
        raise SystemExit(1) from exc

    _console.print(message)


@sentry_digest_command.group(name="schedule")
def sentry_digest_schedule_command() -> None:
    """Manage scheduled Sentry morning digest deliveries."""


@sentry_digest_schedule_command.command(name="add")
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
    "--project",
    "project_slug",
    type=str,
    default="",
    help="Optional Sentry project slug to scope the digest.",
)
def sentry_digest_schedule_add(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    project_slug: str,
) -> None:
    """Schedule daily Sentry morning digest delivery."""
    from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
    from integrations.sentry.digest_prerequisites import (
        require_digest_delivery_provider,
        require_sentry_integration,
    )

    require_sentry_integration()
    require_digest_delivery_provider(provider)
    validate_cron_and_timezone(cron_expr, timezone)

    params: dict[str, str] = {}
    if project_slug.strip():
        params["project_slug"] = project_slug.strip()

    task = ScheduledTask(
        kind=TaskKind.SENTRY_MORNING_DIGEST,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id,
        window_hours=24,
        params=params,
    )
    add_task_and_echo(task, label="Sentry digest")
    if params:
        _console.print(f"  Project: {params['project_slug']}")


@sentry_digest_schedule_command.command(name="list")
def sentry_digest_schedule_list() -> None:
    """List scheduled Sentry morning digest tasks."""
    from infrastructure.scheduling.scheduler.storage import list_tasks
    from infrastructure.scheduling.scheduler.types import TaskKind

    tasks = [task for task in list_tasks() if task.kind == TaskKind.SENTRY_MORNING_DIGEST]
    if not tasks:
        _console.print("[dim]No Sentry morning digest schedules configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Chat")
    table.add_column("Project")
    table.add_column("Enabled")
    table.add_column("Last run")

    for task in tasks:
        project = task.params.get("project_slug", "—")
        table.add_row(
            task.display_id(),
            task.cron,
            task.timezone,
            task.provider.value,
            task.chat_id,
            project or "—",
            GLYPH_SUCCESS if task.enabled else GLYPH_ERROR,
            task.last_run or "—",
        )

    _console.print(table)


@sentry_digest_schedule_command.command(name="remove")
@click.argument("task_id")
def sentry_digest_schedule_remove(task_id: str) -> None:
    """Remove a scheduled Sentry morning digest task."""
    from infrastructure.scheduling.scheduler.storage import get_task, remove_task
    from infrastructure.scheduling.scheduler.types import TaskKind

    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_MORNING_DIGEST:
        _console.print(f"[red]Error: Sentry digest task {task_id} not found.[/red]")
        raise SystemExit(1)

    if remove_task(task_id):
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


@sentry_digest_schedule_command.command(name="run")
@click.argument("task_id")
def sentry_digest_schedule_run(task_id: str) -> None:
    """Run a scheduled Sentry digest task immediately."""
    from bootstrap.adapters import scheduler_runners
    from infrastructure.scheduling.scheduler.runner import run_task_now
    from infrastructure.scheduling.scheduler.storage import get_task
    from infrastructure.scheduling.scheduler.types import TaskKind
    from integrations.sentry.digest_prerequisites import require_digest_delivery_provider

    configure_process(SCHEDULED_COMMAND_PROFILE)
    task = get_task(task_id)
    if task is None or task.kind != TaskKind.SENTRY_MORNING_DIGEST:
        _console.print(f"[red]Error: Sentry digest task {task_id} not found.[/red]")
        raise SystemExit(1)

    require_digest_delivery_provider(task.provider.value)

    _console.print(f"Running Sentry digest task {task_id}...")
    success = run_task_now(task_id, scheduler_runners())
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


__all__ = ["sentry_command"]
