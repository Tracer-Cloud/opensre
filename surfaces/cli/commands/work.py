"""``opensre work`` — durable human task management."""

from __future__ import annotations

import json
from collections.abc import Sequence

import click
from rich.console import Console
from rich.table import Table

from core.domain.work_items import (
    WORK_ITEM_PRIORITIES,
    WorkItemChannelTarget,
    add_work_item,
    complete_work_items,
    cron_from_datetime,
    list_work_items,
    parse_work_item_datetime,
    prioritize_work_items,
    work_items_path,
)
from infrastructure.scheduling.scheduler.storage import add_task as add_scheduled_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_console = Console(highlight=False)


@click.group(name="work")
def work_command() -> None:
    """Manage durable human tasks, reminders, and prioritization."""


@work_command.command(name="list")
@click.option(
    "--status",
    type=click.Choice(
        ["open", "active", "blocked", "deferred", "completed", "all"], case_sensitive=False
    ),
    default="open",
    show_default=True,
)
@click.option("--project", default="", help="Exact project filter.")
@click.option("--owner", default="", help="Exact owner filter.")
@click.option("--json", "json_out", is_flag=True, help="Emit JSON instead of a table.")
def work_list(status: str, project: str, owner: str, json_out: bool) -> None:
    """List work items."""
    if status == "active":
        rows = [
            item
            for item in list_work_items(status=None, project=project, owner=owner)
            if item.is_active
        ]
    else:
        rows = list_work_items(
            status=None if status == "all" else status, project=project, owner=owner
        )
    if json_out:
        click.echo(json.dumps([item.to_dict() for item in rows], indent=2, ensure_ascii=False))
        return
    _render_items(rows)


@work_command.command(name="add")
@click.argument("title", nargs=-1, required=True)
@click.option("--project", default="", help="Project/category.")
@click.option(
    "--priority",
    type=click.Choice(list(WORK_ITEM_PRIORITIES), case_sensitive=False),
    default="normal",
)
@click.option("--owner", default="", help="Owner/person/team responsible.")
@click.option("--notes", default="", help="Supporting detail.")
@click.option("--due-at", default="", help="ISO-like due datetime.")
@click.option("--remind-at", default="", help="ISO-like one-shot reminder datetime.")
@click.option(
    "--provider", default="", help="Reminder provider: telegram, slack, discord, or rocketchat."
)
@click.option("--chat-id", default="", help="Reminder chat/channel id.")
@click.option(
    "--target",
    "targets",
    multiple=True,
    help="Extra delivery target as provider:chat_id; Slack webhook may be just slack.",
)
@click.option(
    "--tz",
    "timezone",
    default="UTC",
    show_default=True,
    help="Timezone for naive remind-at values.",
)
def work_add(
    title: tuple[str, ...],
    project: str,
    priority: str,
    owner: str,
    notes: str,
    due_at: str,
    remind_at: str,
    provider: str,
    chat_id: str,
    targets: tuple[str, ...],
    timezone: str,
) -> None:
    """Add a work item."""
    title_text = " ".join(title).strip()
    _validate_datetime("due-at", due_at)
    _validate_datetime("remind-at", remind_at)
    delivery_targets = _parse_delivery_targets(provider, chat_id, targets)
    item = add_work_item(
        title=title_text,
        project=project,
        priority=priority,
        owner=owner,
        notes=notes,
        due_at=due_at,
        remind_at=remind_at,
        channel_provider=delivery_targets[0].provider if delivery_targets else "",
        channel_id=delivery_targets[0].chat_id if delivery_targets else "",
        channel_targets=delivery_targets,
        source="cli",
    )
    scheduled_id = ""
    if remind_at:
        if not delivery_targets:
            raise click.BadParameter("--remind-at requires --target or --provider/--chat-id")
        remind_dt = parse_work_item_datetime(remind_at)
        if remind_dt is not None:
            primary = delivery_targets[0]
            schedule_timezone = "UTC" if remind_dt.tzinfo is not None else timezone
            scheduled = add_scheduled_task(
                ScheduledTask(
                    kind=TaskKind.WORK_ITEM_REMINDER,
                    cron=cron_from_datetime(remind_dt),
                    timezone=schedule_timezone,
                    provider=Provider(primary.provider),
                    chat_id=primary.chat_id,
                    params={
                        "work_item_id": item.id,
                        "store_path": str(work_items_path()),
                        "disable_after_success": "true",
                        "delivery_targets": json.dumps(
                            [target.to_dict() for target in delivery_targets],
                            separators=(",", ":"),
                        ),
                    },
                )
            )
            scheduled_id = scheduled.id
    _console.print(f"[green]Work item {item.display_id} created.[/green]")
    if scheduled_id:
        _console.print(f"  Reminder scheduled as cron task {scheduled_id}.")
    _console.print(f"  Store: {work_items_path()}")


@work_command.command(name="complete")
@click.argument("selectors", nargs=-1, required=True)
def work_complete(selectors: tuple[str, ...]) -> None:
    """Mark one or more work items completed."""
    result = complete_work_items(selectors)
    if not result.completed and (result.not_found or result.ambiguous):
        _console.print("[red]No work items were completed.[/red]")
        raise SystemExit(1)
    for item in result.completed:
        _console.print(f"[green]Completed {item.display_id}[/green] {item.title}")
    for selector in result.not_found:
        _console.print(f"[red]Not found:[/red] {selector}")
    for selector, candidates in result.ambiguous.items():
        choices = ", ".join(f"{item.display_id}:{item.title}" for item in candidates)
        _console.print(f"[red]Ambiguous:[/red] {selector} ({choices})")


@work_command.command(name="next")
@click.option("--project", default="", help="Exact project filter.")
@click.option("--owner", default="", help="Exact owner filter.")
@click.option("--limit", type=click.IntRange(min=1), default=5, show_default=True)
def work_next(project: str, owner: str, limit: int) -> None:
    """Show the highest-priority open work."""
    items = [
        item
        for item in list_work_items(status=None, project=project, owner=owner)
        if item.is_active
    ]
    ranked = prioritize_work_items(items, limit=limit)
    if not ranked:
        _console.print("[dim]No open work items found.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Rank", justify="right")
    table.add_column("ID", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Title")
    table.add_column("Why")
    for index, scored in enumerate(ranked, start=1):
        table.add_row(
            str(index),
            scored.item.display_id,
            str(scored.score),
            scored.item.title,
            ", ".join(scored.reasons),
        )
    _console.print(table)


@work_command.command(name="schedule-checkin")
@click.option("--cron", "cron_expr", required=True, help="Five-field cron expression.")
@click.option("--tz", "timezone", default="UTC", show_default=True)
@click.option(
    "--provider",
    default="",
    help="Delivery provider: telegram, slack, discord, or rocketchat.",
)
@click.option("--chat-id", default="", help="Target chat/channel id.")
@click.option(
    "--target",
    "targets",
    multiple=True,
    help="Delivery target as provider:chat_id; repeat for fan-out.",
)
@click.option("--project", default="", help="Exact project filter.")
@click.option("--limit", type=click.IntRange(min=1), default=5, show_default=True)
def work_schedule_checkin(
    cron_expr: str,
    timezone: str,
    provider: str,
    chat_id: str,
    targets: tuple[str, ...],
    project: str,
    limit: int,
) -> None:
    """Schedule recurring work-item check-ins."""
    _validate_cron(cron_expr, timezone)
    delivery_targets = _parse_delivery_targets(provider, chat_id, targets)
    if not delivery_targets:
        raise click.BadParameter("schedule-checkin requires --target or --provider/--chat-id")
    primary = delivery_targets[0]
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_CHECKIN,
        cron=cron_expr,
        timezone=timezone,
        provider=Provider(primary.provider),
        chat_id=primary.chat_id,
        params={
            "store_path": str(work_items_path()),
            "project": project,
            "limit": str(limit),
            "delivery_targets": json.dumps(
                [target.to_dict() for target in delivery_targets], separators=(",", ":")
            ),
        },
    )
    added = add_scheduled_task(task)
    _console.print(f"[green]Check-in task {added.id} created.[/green]")
    _console.print(f"  Cron: {added.cron}  TZ: {added.timezone}")


@work_command.command(name="path")
def work_path() -> None:
    """Print the work-item store path."""
    _console.print(str(work_items_path()))


def _render_items(rows: Sequence[object]) -> None:
    from core.domain.work_items import WorkItem

    if not rows:
        _console.print(f"[dim]No matching work items. Store: {work_items_path()}[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Priority")
    table.add_column("Status")
    table.add_column("Project")
    table.add_column("Title")
    table.add_column("Due")
    for row in rows:
        item = row if isinstance(row, WorkItem) else row.item  # type: ignore[attr-defined]
        table.add_row(
            item.display_id,
            item.priority.value,
            item.status.value,
            item.project,
            item.title,
            item.due_at[:16],
        )
    _console.print(table)
    _console.print(f"[dim]Store: {work_items_path()}[/dim]")


def _validate_datetime(label: str, value: str) -> None:
    if value and parse_work_item_datetime(value) is None:
        raise click.BadParameter(
            f"{label} must be ISO-like, e.g. 2026-07-29T09:30 or 2026-07-29T09:30Z"
        )


def _parse_delivery_targets(
    provider: str,
    chat_id: str,
    target_specs: tuple[str, ...],
) -> tuple[WorkItemChannelTarget, ...]:
    parsed: list[WorkItemChannelTarget] = []
    if provider.strip():
        parsed.append(
            WorkItemChannelTarget(provider=provider.strip().lower(), chat_id=chat_id.strip())
        )
    for spec in target_specs:
        provider_part, sep, chat_part = spec.partition(":")
        parsed.append(
            WorkItemChannelTarget(
                provider=provider_part.strip().lower(),
                chat_id=chat_part.strip() if sep else "",
            )
        )

    seen: set[tuple[str, str]] = set()
    unique: list[WorkItemChannelTarget] = []
    for target in parsed:
        if not target.provider:
            continue
        try:
            parsed_provider = Provider(target.provider)
        except ValueError:
            raise click.BadParameter(
                f"unknown provider {target.provider!r}; use telegram, slack, discord, or rocketchat"
            ) from None
        if parsed_provider is not Provider.SLACK and not target.chat_id:
            raise click.BadParameter(f"{target.provider} target requires a chat/channel id")
        key = (target.provider, target.chat_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return tuple(unique)


def _validate_cron(cron_expr: str, timezone: str) -> None:
    if len(cron_expr.split()) != 5:
        raise click.BadParameter("cron must have exactly 5 fields")
    try:
        from apscheduler.triggers.cron import CronTrigger

        CronTrigger.from_crontab(cron_expr, timezone=timezone)
    except (KeyError, TypeError, ValueError) as exc:
        raise click.BadParameter(str(exc)) from None


__all__ = ["work_command"]
