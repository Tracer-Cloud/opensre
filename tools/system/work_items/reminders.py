"""Scheduled reminder helpers for work items."""

from __future__ import annotations

import json

from config.constants.work_items import WORK_ITEM_REMINDER_RUN_AT_PARAM
from core.domain.work_items import (
    WorkItem,
    WorkItemChannelTarget,
    parse_work_item_datetime,
    resolve_work_item_datetime,
    work_items_path,
)
from infrastructure.scheduling.scheduler.storage import add_task as add_scheduled_task
from infrastructure.scheduling.scheduler.storage import list_tasks, update_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tools.system.work_items.validation import validate_provider


def disable_existing_item_reminders(item_id: str) -> int:
    """Disable enabled one-shot reminders for ``item_id`` so updates replace them."""
    disabled = 0
    for task in list_tasks():
        if task.kind is not TaskKind.WORK_ITEM_REMINDER:
            continue
        if not task.enabled:
            continue
        if task.params.get("work_item_id", "").strip() != item_id:
            continue
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
    schedule_timezone = timezone.strip() or "UTC"
    parsed_remind_at = parse_work_item_datetime(item.remind_at)
    remind_at = resolve_work_item_datetime(item.remind_at, schedule_timezone)
    if remind_at is None:
        return None
    if parsed_remind_at is not None and parsed_remind_at.tzinfo is not None:
        schedule_timezone = "UTC"
    valid_targets = [target for target in targets if validate_provider(target.provider) is not None]
    if not valid_targets:
        return None
    primary = valid_targets[0]
    parsed_provider = Provider(primary.provider)
    task = ScheduledTask(
        kind=TaskKind.WORK_ITEM_REMINDER,
        cron="",
        timezone=schedule_timezone,
        provider=parsed_provider,
        chat_id=primary.chat_id,
        params={
            "work_item_id": item.id,
            "store_path": str(work_items_path()),
            "disable_after_success": "true",
            WORK_ITEM_REMINDER_RUN_AT_PARAM: remind_at.isoformat(),
            "delivery_targets": json.dumps(
                [target.to_dict() for target in valid_targets], separators=(",", ":")
            ),
        },
    )
    # Disable prior reminders only when the schedule is actually changing.
    # When the same remind_at/target combination is confirmed again (e.g. a title
    # rename), add_task would return the already-disabled row unchanged, leaving
    # the reminder permanently disabled. Skip the disable so add_task finds an
    # enabled row and returns it directly.
    existing_enabled = next(
        (
            t
            for t in list_tasks()
            if t.kind is TaskKind.WORK_ITEM_REMINDER
            and t.enabled
            and t.params.get("work_item_id", "").strip() == item.id
        ),
        None,
    )
    same_schedule = (
        existing_enabled is not None
        and existing_enabled.params.get(WORK_ITEM_REMINDER_RUN_AT_PARAM) == remind_at.isoformat()
    )
    if not same_schedule:
        disable_existing_item_reminders(item.id)
    stored = add_scheduled_task(task)
    if stored is not None and not stored.enabled:
        re_enabled = stored.model_copy(update={"enabled": True})
        update_task(re_enabled)
        return re_enabled
    return stored


_disable_existing_item_reminders = disable_existing_item_reminders
_schedule_item_reminder = schedule_item_reminder

__all__ = [
    "_disable_existing_item_reminders",
    "_schedule_item_reminder",
    "disable_existing_item_reminders",
    "schedule_item_reminder",
]
