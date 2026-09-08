"""``opensre cron`` command group: manage scheduled deliveries.

Provides CLI surface for creating, listing, removing, running, and
viewing logs of cron-driven scheduled tasks that deliver reports to
messaging providers.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from core.agent_harness import pin_recurring_skill, validate_skill_inputs
from infrastructure.scheduling.scheduler.credentials import requires_explicit_chat_id
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.types import Provider, TaskKind, TaskRun, TaskStatus
from infrastructure.terminal.theme import GLYPH_ERROR, GLYPH_SUCCESS
from surfaces.cli.commands.scheduling import validate_cron_and_timezone

_console = Console()

# Sentry-kind tasks are created and listed only through `opensre sentry
# digest`/`opensre sentry uptime watch` (dedicated Sentry-integration setup,
# project_slug handling), not through this generic command group, so they
# are deliberately excluded from --kind here rather than a hand-typed list
# that happens to match.
_CRON_ADD_SUPPORTED_KINDS: tuple[TaskKind, ...] = tuple(
    kind
    for kind in TaskKind
    if kind not in (TaskKind.SENTRY_MORNING_DIGEST, TaskKind.SENTRY_UPTIME_WATCH)
)
_KIND_CHOICES = [k.value for k in _CRON_ADD_SUPPORTED_KINDS]
_PROVIDER_CHOICES = [p.value for p in Provider]


@click.group(name="cron")
def cron_command() -> None:
    """Manage cron-driven scheduled deliveries to messaging providers."""


@cron_command.command(name="add")
@click.option(
    "--name",
    type=str,
    default="",
    show_default=False,
    help="Human-readable loop name for list output.",
)
@click.option(
    "--kind",
    type=click.Choice(_KIND_CHOICES, case_sensitive=False),
    required=True,
    help="The kind of scheduled task.",
)
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
    default="",
    show_default=False,
    help=(
        "Chat/channel ID for the target provider. Required unless the "
        "provider already has a configured destination, such as a webhook "
        "is configured (the webhook's bound channel is the destination)."
    ),
)
@click.option(
    "--window",
    "window_hours",
    type=click.IntRange(min=1),
    default=24,
    show_default=True,
    help="Lookback window in hours for the report (must be >= 1).",
)
@click.option(
    "--prompt",
    type=str,
    default="",
    show_default=False,
    help="Instruction to execute on each manual_loop run.",
)
@click.option(
    "--skill",
    "skill_name",
    type=str,
    default="",
    show_default=False,
    help="Recurring action skill to run (required for recurring_skill kind).",
)
@click.option("--owner", type=str, default="", help="GitHub repository owner.")
@click.option("--repo", type=str, default="", help="GitHub repository name.")
@click.option("--branch", type=str, default="", help="Optional GitHub branch filter.")
@click.option(
    "--pr", "pr_number", type=click.IntRange(min=1), default=None, help="Optional GitHub PR filter."
)
@click.option("--city", type=str, default="", help="Optional city for the morning-report skill.")
def cron_add(
    name: str,
    kind: str,
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    window_hours: int,
    prompt: str,
    skill_name: str,
    owner: str,
    repo: str,
    branch: str,
    pr_number: int | None,
    city: str,
) -> None:
    """Add a new scheduled delivery task."""
    from infrastructure.scheduling.scheduler.types import ScheduledTask

    # Validate cron expression by constructing the APScheduler trigger
    validate_cron_and_timezone(cron_expr, timezone)
    _validate_chat_id_for_provider(provider, chat_id)

    task_kind = TaskKind(kind)
    normalized_prompt = prompt.strip()
    if task_kind == TaskKind.MANUAL_LOOP:
        if not normalized_prompt:
            raise click.ClickException("--prompt is required when --kind is manual_loop.")
    elif normalized_prompt:
        raise click.ClickException("--prompt is only valid with --kind manual_loop.")
    pinned_name = ""
    pinned_revision = ""
    if task_kind == TaskKind.RECURRING_SKILL:
        if not skill_name.strip():
            raise click.ClickException("--skill is required when --kind is recurring_skill.")
        try:
            pinned_name, pinned_revision = pin_recurring_skill(skill_name)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
    elif skill_name.strip():
        raise click.ClickException("--skill is only valid with --kind recurring_skill.")
    skill_inputs = _recurring_skill_inputs(
        pinned_name,
        city=city,
        owner=owner,
        repo=repo,
        branch=branch,
        pr_number=pr_number,
    )
    task_params = {LOOP_PROMPT_PARAM: normalized_prompt} if normalized_prompt else {}

    task = ScheduledTask(
        name=name.strip(),
        kind=task_kind,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(provider),
        chat_id=chat_id.strip(),
        window_hours=window_hours,
        skill_name=pinned_name,
        skill_revision=pinned_revision,
        skill_inputs=skill_inputs,
        params=task_params,
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
    if added.skill_name:
        _console.print(f"  Skill: {added.skill_name}  Revision: {added.skill_revision[:12]}…")
    _console.print(f"  Provider: {added.provider.value}  Chat: {added.chat_id}")


def _recurring_skill_inputs(
    skill_name: str,
    *,
    city: str,
    owner: str,
    repo: str,
    branch: str,
    pr_number: int | None,
) -> dict[str, str]:
    """Validate and serialize inputs for the selected recurring skill."""
    normalized_city = city.strip()
    values_supplied = bool(owner.strip() or repo.strip() or branch.strip() or pr_number)
    if skill_name == "morning-report":
        if values_supplied:
            raise click.UsageError(
                "--owner, --repo, --branch, and --pr are only valid with "
                "--kind recurring_skill --skill github-ci-health."
            )
        return validate_skill_inputs({"city": normalized_city} if normalized_city else {})
    if normalized_city:
        raise click.UsageError(
            "--city is only valid with --kind recurring_skill --skill morning-report."
        )
    if skill_name != "github-ci-health":
        if values_supplied:
            raise click.UsageError(
                "--owner, --repo, --branch, and --pr are only valid with "
                "--kind recurring_skill --skill github-ci-health."
            )
        return validate_skill_inputs({})
    if not owner.strip() or not repo.strip():
        raise click.UsageError("--owner and --repo are required for skill github-ci-health.")
    if branch.strip() and pr_number is not None:
        raise click.UsageError("Use either --branch or --pr, not both.")
    params = {"owner": owner.strip(), "repo": repo.strip()}
    if branch.strip():
        params["branch"] = branch.strip()
    if pr_number is not None:
        params["pr_number"] = str(pr_number)
    return validate_skill_inputs(params)


@cron_command.command(name="list")
def cron_list() -> None:
    """List all scheduled delivery tasks."""
    from infrastructure.scheduling.scheduler.loops import list_loop_summaries

    loops = list_loop_summaries()
    if not loops:
        _console.print("[dim]No scheduled tasks configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Cron")
    table.add_column("TZ")
    table.add_column("Provider")
    table.add_column("Channels")
    table.add_column("Enabled")
    table.add_column("Next Run")
    table.add_column("Last Run")

    for loop in loops:
        table.add_row(
            loop.id[:12],
            loop.name,
            loop.kind.value,
            loop.cron,
            loop.timezone,
            loop.provider.value,
            ", ".join(loop.channels),
            GLYPH_SUCCESS if loop.enabled else GLYPH_ERROR,
            loop.next_run or "—",
            loop.last_run or "—",
        )

    _console.print(table)


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
    success = run_task_now(task_id, scheduler_runners(), only_failed=failed_only)
    if success:
        _console.print("[green]Done.[/green]")
    else:
        _console.print("[red]Task execution failed. Check logs for details.[/red]")
        raise SystemExit(1)


def _delivered_targets(run: TaskRun) -> str:
    """How many of a run's destinations were delivered to (``2/3``)."""
    if not run.targets:
        return "—"
    return f"{sum(1 for outcome in run.targets if outcome.ok)}/{len(run.targets)}"


def _run_status_label(run: TaskRun) -> str:
    """Describe whether a run was abandoned or recovered by a later attempt."""
    if run.status is TaskStatus.ABANDONED:
        return "abandoned"
    if run.attempt > 1:
        return f"reclaimed/{run.status.value}"
    return run.status.value


@cron_command.command(name="logs")
@click.argument("task_id")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Max number of runs to show (must be >= 1).",
)
def cron_logs(task_id: str, limit: int) -> None:
    """Show execution history for a scheduled task."""
    from infrastructure.scheduling.scheduler.storage import get_runs, get_task

    task = get_task(task_id)
    if task is None:
        _console.print(f"[red]Error: task {task_id} not found.[/red]")
        raise SystemExit(1)

    runs = get_runs(task_id, limit=limit)
    if not runs:
        _console.print(f"[dim]No execution history for task {task_id}.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Started")
    table.add_column("Attempt")
    table.add_column("Status")
    table.add_column("Targets")
    table.add_column("Message ID")
    table.add_column("Error")

    for run in runs:
        status_style = (
            "green"
            if run.status.value == "success"
            else "red"
            if run.status.value in {"failed", "abandoned"}
            else ""
        )
        status_label = _run_status_label(run)
        table.add_row(
            run.started_at,
            str(run.attempt),
            f"[{status_style}]{status_label}[/{status_style}]" if status_style else status_label,
            _delivered_targets(run),
            run.posted_message_id or "—",
            run.error[:50] if run.error else "—",
        )

    _console.print(table)


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


def _validate_chat_id_for_provider(provider: str, chat_id: str) -> None:
    """Reject a task with no destination the scheduler could deliver to.

    Which providers can resolve a destination on their own is the scheduler's
    knowledge, not the CLI's — see
    :func:`infrastructure.scheduling.scheduler.credentials.requires_explicit_chat_id`.
    """
    if chat_id.strip() or not requires_explicit_chat_id(provider):
        return
    _console.print(f"[red]Error: --chat-id is required for provider {provider}.[/red]")
    _console.print("  This provider has no configured destination to fall back on.")
    raise SystemExit(2)


__all__ = ["cron_command"]
