"""Scheduled reminder helpers for work items."""

from __future__ import annotations

import json

from filelock import FileLock

from config.constants.work_items import WORK_ITEM_REMINDER_RUN_AT_PARAM
from core.domain.work_items import (
    WorkItem,
    WorkItemChannelTarget,
    parse_work_item_datetime,
    resolve_work_item_datetime,
    work_items_path,
)
from infrastructure.scheduling.scheduler.storage import add_task as add_scheduled_task
from infrastructure.scheduling.scheduler.storage import list_tasks, patch_task_definitions
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tools.system.work_items.validation import validate_provider

_REMINDER_LOCK_FILENAME = ".work_item_reminders.lock"


def _reminder_replacement_lock() -> FileLock:
    """Serialize reminder replacement, so overlapping updates leave exactly one enabled."""
    return FileLock(str(work_items_path().parent / _REMINDER_LOCK_FILENAME), timeout=10)


def disable_existing_item_reminders(item_id: str, *, keep_task_id: str = "") -> int:
    """Disable enabled one-shot reminders for ``item_id``, except ``keep_task_id``."""
    disabled = 0
    for task in list_tasks():
        if task.kind is not TaskKind.WORK_ITEM_REMINDER:
            continue
        if not task.enabled or task.id == keep_task_id:
            continue
        if task.params.get("work_item_id", "").strip() != item_id:
            continue
        # Patch the locked row, so a concurrent edit to it is not overwritten.
        if patch_task_definitions((task.id,), lambda _current: {"enabled": False}):
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
    with _reminder_replacement_lock():
        scheduled = add_scheduled_task(task)
        if not scheduled.enabled:
            # add_task returns a stored reminder with the same schedule even when it
            # is disabled; scheduling it again means it should fire.
            enabled = patch_task_definitions((scheduled.id,), lambda _current: {"enabled": True})
            scheduled = enabled[0] if enabled else scheduled
        # The new reminder replaces any other one for this work item.
        disable_existing_item_reminders(item.id, keep_task_id=scheduled.id)
    return scheduled


_disable_existing_item_reminders = disable_existing_item_reminders
_schedule_item_reminder = schedule_item_reminder

__all__ = [
    "_disable_existing_item_reminders",
    "_schedule_item_reminder",
    "disable_existing_item_reminders",
    "schedule_item_reminder",
]
