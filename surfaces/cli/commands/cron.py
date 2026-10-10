"""``opensre cron`` command group: manage scheduled deliveries.

Provides CLI surface for creating, listing, removing, running, and
viewing logs of cron-driven scheduled tasks that deliver reports to
messaging providers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
from rich.console import Console, Group
from rich.text import Text

if TYPE_CHECKING:
    from infrastructure.scheduling.scheduler.loops import LoopSummary

from infrastructure.process.runtime_flags import is_json_output
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_MODE_PARAM,
    LOOP_SKILL_PARAM,
    LOOP_STATELESS_PARAM,
    LOOP_TEMPLATE_PARAM,
)
from infrastructure.scheduling.scheduler.types import TaskKind
from infrastructure.terminal.theme import BOLD_BRAND, DIM
from surfaces.shared.cron_options import cron_add_parameters
from surfaces.shared.cron_tasks import prepare_cron_task
from surfaces.shared.terminal.tables.schedule_listing import print_loop_schedules

_console = Console()

_STATUS_STORAGE_TIMEOUT_SECONDS = 1.0


def _format_duration(seconds: float | None) -> str:
    """Render an operational age without false precision."""
    if seconds is None:
        return "—"
    total_seconds = int(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m"


@click.group(name="cron")
def cron_command() -> None:
    """Manage cron-driven scheduled deliveries to messaging providers."""


@cron_command.command(name="add", params=cron_add_parameters())
def cron_add(
    name: str,
    description: str,
    kind: str,
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    window_hours: int,
    prompt: str,
    template: str | None,
    mode: str | None,
    skill_name: str,
    stateless: bool,
    owner: str,
    repo: str,
    branch: str,
    github_connection_id: str,
    pr_number: int | None,
    city: str,
) -> None:
    """Add a new scheduled delivery task."""
    task = prepare_cron_task(
        name=name,
        description=description,
        kind=kind,
        cron_expr=cron_expr,
        timezone=timezone,
        provider=provider,
        chat_id=chat_id,
        window_hours=window_hours,
        prompt=prompt,
        template=template,
        mode=mode,
        skill_name=skill_name,
        stateless=stateless,
        owner=owner,
        repo=repo,
        branch=branch,
        github_connection_id=github_connection_id,
        pr_number=pr_number,
        city=city,
    )

    from infrastructure.scheduling.scheduler.operation_log import record_scheduler_task_operation
    from infrastructure.scheduling.scheduler.storage import add_task

    added = add_task(task)
    record_scheduler_task_operation(
        "scheduled_task_created",
        added,
        extra={
            "command": "cron_add",
            "requested_task_id": task.id,
            "deduplicated": added.id != task.id,
        },
    )
    _console.print(f"[green]Task {added.id} created.[/green]")
    if added.name:
        _console.print(f"  Name: {added.name}")
    _console.print(f"  Kind: {added.kind.value}  Cron: {added.cron}  TZ: {added.timezone}")
    if added.kind is TaskKind.MANUAL_LOOP:
        stateless_note = " (stateless)" if added.params.get(LOOP_STATELESS_PARAM) else ""
        _console.print(f"  Mode: {added.params.get(LOOP_MODE_PARAM, 'report')}{stateless_note}")
    if added.params.get(LOOP_TEMPLATE_PARAM):
        _console.print(f"  Template: {added.params[LOOP_TEMPLATE_PARAM]}")
    if added.skill_name:
        _console.print(f"  Skill: {added.skill_name}  Revision: {added.skill_revision[:12]}…")
    if added.params.get(LOOP_SKILL_PARAM):
        _console.print(f"  Skill: {added.params[LOOP_SKILL_PARAM]}")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")


def _cron_task_json(loop: LoopSummary) -> dict[str, object]:
    """Return the stable machine-readable view of a scheduled loop."""
    return {
        "id": loop.id,
        "task_ids": list(loop.task_ids),
        "name": loop.name,
        "description": loop.description,
        "kind": loop.kind.value,
        "cron": loop.cron,
        "timezone": loop.timezone,
        "provider": loop.provider.value,
        "chat_id": loop.chat_id,
        "channels": list(loop.channels),
        "enabled": loop.enabled,
        "status": loop.status,
        "last_run": loop.last_run,
        "next_run": loop.next_run,
        "schedule_error": loop.schedule_error,
        "mode": loop.mode,
    }


@cron_command.command(name="list")
def cron_list() -> None:
    """List all scheduled delivery tasks."""
    import json

    from infrastructure.scheduling.scheduler.loops import list_loop_summaries

    loops = list_loop_summaries()
    if is_json_output():
        _console.print_json(json.dumps([_cron_task_json(loop) for loop in loops]))
        return
    if not loops:
        _console.print("[dim]No scheduled tasks configured.[/dim]")
        return

    print_loop_schedules(_console, loops)


def _unknown_backlog_status(as_json: bool, error: str) -> click.exceptions.Exit:
    """Render unavailable backlog metrics and return the failure exit."""
    import json

    if as_json:
        _console.print_json(
            json.dumps(
                {
                    "status": "unknown",
                    "pending_count": None,
                    "oldest_pending_at": None,
                    "oldest_pending_age_seconds": None,
                    "error": error,
                }
            )
        )
    else:
        _console.print(
            "[red]Error: scheduler storage is unreadable; backlog status is unknown.[/red]"
        )
    return click.exceptions.Exit(1)


@cron_command.command(name="status")
@click.option("--json", "as_json", is_flag=True, help="Return structured backlog state.")
def cron_status(as_json: bool) -> None:
    """Show durable scheduler backlog pressure."""
    import json
    import sqlite3

    from infrastructure.scheduling.scheduler.storage import (
        BacklogStatusRunStoreError,
        get_backlog_snapshot,
        get_task_store_snapshot,
    )

    as_json = as_json or is_json_output()
    try:
        task_store = get_task_store_snapshot(lock_timeout_seconds=_STATUS_STORAGE_TIMEOUT_SECONDS)
    except BacklogStatusRunStoreError:
        raise _unknown_backlog_status(as_json, "run_store_unreadable") from None
    except OSError:
        raise _unknown_backlog_status(as_json, "task_store_unreadable") from None
    if not task_store.complete:
        raise _unknown_backlog_status(as_json, "task_store_unreadable")

    try:
        snapshot = get_backlog_snapshot(eligible_task_ids={task.id for task in task_store.tasks})
    except (OSError, sqlite3.Error):
        raise _unknown_backlog_status(as_json, "run_store_unreadable") from None
    oldest_pending_at = (
        snapshot.oldest_pending_at.isoformat() if snapshot.oldest_pending_at is not None else None
    )
    if as_json:
        _console.print_json(
            json.dumps(
                {
                    "status": "ok",
                    "pending_count": snapshot.pending_count,
                    "oldest_pending_at": oldest_pending_at,
                    "oldest_pending_age_seconds": snapshot.oldest_pending_age_seconds,
                }
            )
        )
        return

    _console.print(
        Group(
            Text("Scheduler status", style=BOLD_BRAND),
            Text(f"Pending runs: {snapshot.pending_count}"),
            Text(f"Oldest pending: {oldest_pending_at or '—'}", style=DIM),
            Text(
                f"Oldest pending age: {_format_duration(snapshot.oldest_pending_age_seconds)}",
                style=DIM,
            ),
        )
    )


@cron_command.command(name="remove")
@click.argument("task_id")
def cron_remove(task_id: str) -> None:
    """Remove a scheduled delivery task by ID."""
    from infrastructure.scheduling.scheduler.operation_log import record_scheduler_task_operation
    from infrastructure.scheduling.scheduler.storage import get_task, remove_task

    task = get_task(task_id)
    if remove_task(task_id):
        if task is not None:
            record_scheduler_task_operation(
                "scheduled_task_deleted",
                task,
                extra={"command": "cron_remove"},
            )
        _console.print(f"[green]Task {task_id} removed.[/green]")
    else:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)


def _warn_if_rerun_duplicates(task_id: str) -> None:
    """Warn before a full rerun re-posts where the last run already delivered.

    A partial failure is the case an operator is most likely to reach for
    ``cron run`` to fix, and a full rerun is the one thing that quietly
    double-posts. Warn rather than narrow the delivery silently: a plain
    ``cron run`` is also the way to trigger a task on demand, and that has to
    keep reaching every destination.
    """
    from infrastructure.scheduling.scheduler.storage import get_latest_targeted_run

    run = get_latest_targeted_run(task_id)
    if run is None:
        return
    delivered = [outcome for outcome in run.targets if outcome.ok]
    if not delivered or len(delivered) == len(run.targets):
        return
    names = ", ".join(outcome.label() for outcome in delivered)
    _console.print(
        f"[yellow]Note: the most recent run already delivered to {names}. "
        "This re-sends there too — use --failed-only to retry just the "
        "destinations that failed.[/yellow]"
    )


@cron_command.command(name="run")
@click.argument("task_id")
@click.option(
    "--failed-only",
    is_flag=True,
    default=False,
    help="Retry only the destinations the most recent run failed at, instead of "
    "delivering to every configured destination again.",
)
def cron_run(task_id: str, failed_only: bool) -> None:
    """Run a scheduled task immediately (ad-hoc one-shot for debugging)."""
    from bootstrap.adapters import scheduler_runners
    from bootstrap.process import SCHEDULED_COMMAND_PROFILE, configure_process
    from infrastructure.scheduling.scheduler.operation_log import record_scheduler_task_operation
    from infrastructure.scheduling.scheduler.runner import failed_retry_scope, run_task_now
    from infrastructure.scheduling.scheduler.storage import get_task

    configure_process(SCHEDULED_COMMAND_PROFILE)

    task = get_task(task_id)
    if task is None:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)

    if failed_only:
        scope = failed_retry_scope(task_id)
        if scope is None:
            _console.print(
                "[red]No readable per-target history for this task, so which "
                "destinations failed is unknown.[/red]"
            )
            _console.print("Run without --failed-only to deliver to every configured destination.")
            raise SystemExit(1)
        if not scope:
            _console.print("[dim]Nothing to retry — the most recent run had no failures.[/dim]")
            return
    else:
        _warn_if_rerun_duplicates(task_id)

    _console.print(f"Running task {task_id} ({task.kind.value})...")
    record_scheduler_task_operation(
        "scheduled_task_run_requested",
        task,
        extra={"command": "cron_run", "failed_only": failed_only},
    )
    from surfaces.cli.commands.cron_results import print_run_result

    success = run_task_now(
        task_id,
        scheduler_runners(),
        only_failed=failed_only,
        on_result=lambda run: print_run_result(_console, run),
    )
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


@cron_command.command(name="logs")
@click.argument("task_id")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Max number of runs to show (must be >= 1).",
)
@click.option(
    "--run", "run_id", type=click.IntRange(min=1), default=None, help="Show one retained run."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Return structured execution and delivery outcomes."
)
def cron_logs(task_id: str, limit: int, run_id: int | None, as_json: bool) -> None:
    """Show execution history for a scheduled task."""
    import json

    from infrastructure.scheduling.scheduler.loop_results import restore_legacy_reports
    from infrastructure.scheduling.scheduler.storage import get_group_run, get_runs
    from surfaces.cli.commands.cron_results import print_run_history, print_run_result

    selected = get_group_run((task_id,), run_id) if run_id is not None else None
    runs = (
        ([selected] if selected is not None else [])
        if run_id is not None
        else get_runs(task_id, limit=limit)
    )
    runs = restore_legacy_reports(runs)
    if as_json:
        _console.print_json(json.dumps([run.model_dump(mode="json") for run in runs]))
        return
    if not runs:
        _console.print(f"[dim]No execution history for task {task_id}.[/dim]")
        return

    print_run_history(_console, runs)
    print_run_result(_console, runs[0])


@cron_command.command(name="start")
@click.option(
    "--service",
    is_flag=True,
    default=False,
    help="Run as a long-lived service: idle and wait when no tasks are enabled, "
    "instead of exiting (for a dedicated MODE=scheduler deployment).",
)
def cron_start(service: bool) -> None:
    """Start the scheduler daemon (blocks until interrupted)."""
    from bootstrap.adapters import scheduler_runners
    from bootstrap.process import SCHEDULER_WORKER_PROFILE, configure_process
    from infrastructure.scheduling.scheduler.runner import start_scheduler

    # Dedicated scheduler process — not SCHEDULED_COMMAND (one-shot CLI helpers).
    configure_process(SCHEDULER_WORKER_PROFILE)

    _console.print("[bold]Starting scheduler daemon...[/bold]")
    _console.print("Press Ctrl+C to stop.")
    start_scheduler(scheduler_runners(), idle_when_empty=service)


__all__ = ["cron_command"]
