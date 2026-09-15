"""Scheduled reminder helpers for work items."""

from __future__ import annotations

import json

from core.domain.work_items import (
    WorkItem,
    WorkItemChannelTarget,
    cron_from_datetime,
    parse_work_item_datetime,
    work_items_path,
)
from infrastructure.scheduling.scheduler.storage import list_tasks, replace_task, update_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tools.system.work_items.validation import validate_provider


def enabled_item_reminders(item_id: str) -> list[ScheduledTask]:
    """Every enabled one-shot reminder currently scheduled for ``item_id``."""
    return [
        task
        for task in list_tasks()
        if task.kind is TaskKind.WORK_ITEM_REMINDER
        and task.enabled
        and task.params.get("work_item_id", "").strip() == item_id
    ]


def existing_reminder_timezone(item_id: str) -> str:
    """Timezone the live reminder for ``item_id`` was scheduled in, or ``""``.

    A work item stores its reminder time but not the zone it was read in, so an
    edit that does not restate the time has to recover the zone from the
    schedule or a naive time silently moves to another hour.
    """
    scheduled = enabled_item_reminders(item_id)
    return scheduled[-1].timezone if scheduled else ""


def disable_existing_item_reminders(item_id: str) -> int:
    """Disable every enabled one-shot reminder for ``item_id``."""
    disabled = 0
    for task in enabled_item_reminders(item_id):
        task.enabled = False
        if update_task(task):
            disabled += 1
    return disabled


def schedule_item_reminder(
    item: WorkItem,
    *,
    targets: list[WorkItemChannelTarget],
    timezone: str,
) -> ScheduledTask | None:
    """Schedule or replace a one-shot work item reminder task."""
    if not item.remind_at:
        return None
    remind_at = parse_work_item_datetime(item.remind_at)
    if remind_at is None:
        return None
    valid_targets = [target for target in targets if validate_provider(target.provider) is not None]
    if not valid_targets:
        return None
    superseded = enabled_item_reminders(item.id)
    primary = valid_targets[0]
    parsed_provider = Provider(primary.provider)
    schedule_timezone = "UTC" if remind_at.tzinfo is not None else timezone
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron=cron_from_datetime(remind_at),
        timezone=schedule_timezone,
        provider=parsed_provider,
        chat_id=primary.chat_id,
        params={
            "work_item_id": item.id,
            "store_path": str(work_items_path()),
            "disable_after_success": "true",
            "delivery_targets": json.dumps(
                [target.to_dict() for target in valid_targets], separators=(",", ":")
            ),
        },
    )
    # One atomic store write: adding first could leave both reminders enabled,
    # disabling first could leave none at all.
    return replace_task(task, disable_task_ids=[prior.id for prior in superseded])


_disable_existing_item_reminders = disable_existing_item_reminders
_schedule_item_reminder = schedule_item_reminder

__all__ = [
    "_disable_existing_item_reminders",
    "_schedule_item_reminder",
    "disable_existing_item_reminders",
    "enabled_item_reminders",
    "existing_reminder_timezone",
    "schedule_item_reminder",
]
