"""Migrate scheduler rows written before retired task kinds were removed."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from filelock import Timeout

from config.constants.work_items import WORK_ITEM_REMINDER_RUN_AT_PARAM
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_LEGACY_TASK_KIND_PARAM,
    LOOP_MIGRATION_NOTICE_PARAM,
    LOOP_PROMPT_PARAM,
)
from infrastructure.scheduling.scheduler.types import TaskKind

_CUSTOM_INVESTIGATION = "custom_investigation"
_RETIRED_TASK_KINDS = frozenset(
    {
        _CUSTOM_INVESTIGATION,
        "daily_summary",
        "weekly_audit",
        "incident_window_replay",
        "synthetic_run",
    }
)


def migrate_legacy_task_entries(entries: Iterable[object]) -> bool:
    """Normalize retired task kinds in place before strict model validation."""
    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if _migrate_legacy_work_item_reminder(entry):
            changed = True
        legacy_kind = entry.get("kind")
        if legacy_kind not in _RETIRED_TASK_KINDS:
            continue

        params = entry.get("params")
        if not isinstance(params, dict):
            params = {}
            entry["params"] = params

        prompt = params.get(LOOP_PROMPT_PARAM)
        entry["kind"] = TaskKind.MANUAL_LOOP.value
        changed = True
        if legacy_kind == _CUSTOM_INVESTIGATION and isinstance(prompt, str) and prompt.strip():
            continue

        task_id = str(entry.get("id") or "<task-id>")
        entry["enabled"] = False
        params[LOOP_LEGACY_TASK_KIND_PARAM] = legacy_kind
        params[LOOP_MIGRATION_NOTICE_PARAM] = (
            f"Legacy task kind '{legacy_kind}' depended on the retired investigation scheduler "
            "and was disabled. Recreate it with 'opensre cron add --kind manual_loop "
            f"--prompt <instruction> ...', then remove this task with 'opensre cron remove {task_id}'."
        )
    return changed


def _migrate_legacy_work_item_reminder(entry: dict[str, object]) -> bool:
    """Add an absolute fire time to one legacy work-item reminder entry."""
    if entry.get("kind") != TaskKind.WORK_ITEM_REMINDER.value:
        return False
    params = entry.get("params")
    if not isinstance(params, dict):
        return False
    run_at = params.get(WORK_ITEM_REMINDER_RUN_AT_PARAM)
    if isinstance(run_at, str) and run_at.strip():
        return False
    item_id = params.get("work_item_id")
    store_path = params.get("store_path")
    timezone = entry.get("timezone")
    if not isinstance(item_id, str) or not item_id.strip():
        return False
    if not isinstance(store_path, str) or not store_path.strip():
        return False
    if not isinstance(timezone, str) or not timezone.strip():
        return False

    from core.domain.work_items import (
        WorkItemStoreError,
        get_work_item,
        resolve_work_item_datetime,
    )

    try:
        item = get_work_item(item_id, store_path=Path(store_path).expanduser())
        if item is None:
            return False
        resolved = resolve_work_item_datetime(item.remind_at, timezone)
    except (OSError, Timeout, ValueError, WorkItemStoreError):
        return False
    if resolved is None:
        return False
    params[WORK_ITEM_REMINDER_RUN_AT_PARAM] = resolved.isoformat()
    return True


__all__ = ["migrate_legacy_task_entries"]
