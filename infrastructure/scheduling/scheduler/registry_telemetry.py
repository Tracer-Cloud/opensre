"""Report the scheduler's saved tasks to analytics.

Dashboards list a gateway's loops from this event instead of reading the
gateway's task store. Each registration pass (start, reload, store change)
reports the whole store; an unchanged store is reported again only after
``_REPORT_REFRESH_SECONDS``, because a capture can be dropped after it returns.
Only display fields cross: task params can hold provider tokens, so params and
skill inputs are reduced to an allowlist and every text field is redacted and
capped. The task list is bounded by key count and serialized size.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

from infrastructure.safety.secret_redaction import redact_text
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CREATED_BY_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_GROUP_ID_PARAM,
    LOOP_MODE_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_SLUG_PARAM,
)
from infrastructure.scheduling.scheduler.storage import get_task_store_snapshot
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_REPOSITORY_KEYS = ("owner", "repo", "repository", "branch", "pr_number")
_PARAM_KEYS = (
    LOOP_GROUP_ID_PARAM,
    LOOP_SLUG_PARAM,
    LOOP_MODE_PARAM,
    LOOP_DESCRIPTION_PARAM,
    LOOP_PROMPT_PARAM,
    LOOP_CREATED_BY_PARAM,
    *_REPOSITORY_KEYS,
)
_TEXT_LIMITS = {LOOP_PROMPT_PARAM: 4000, LOOP_DESCRIPTION_PARAM: 500}
_DEFAULT_TEXT_LIMIT = 200
#: The webapp accepts 512 property keys per event; leave room for base properties.
_TASK_KEY_BUDGET = 320
#: Half the 256 KiB payload limit, measured as ASCII-escaped JSON (an upper bound).
_TASK_BYTE_BUDGET = 128 * 1024
_REPORT_REFRESH_SECONDS = 30 * 60

_last_reported: tuple[str, float] | None = None
_report_lock = threading.Lock()


def _bounded(value: str, key: str = "") -> str:
    text = redact_text(value).strip()
    limit = _TEXT_LIMITS.get(key, _DEFAULT_TEXT_LIMIT)
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _allowed(values: dict[str, str], keys: tuple[str, ...]) -> dict[str, str]:
    return {key: _bounded(values[key], key) for key in keys if values.get(key, "").strip()}


def registry_entry(task: ScheduledTask) -> dict[str, Any]:
    """Project one task to its display fields, in the task store's own shape."""
    entry: dict[str, Any] = {
        "id": task.id,
        "kind": task.kind.value,
        "cron": task.cron,
        "timezone": task.timezone,
        "provider": task.provider.value,
        "enabled": task.enabled,
        "created_at": task.created_at,
    }
    optional = {
        "name": task.name,
        "chat_id": task.chat_id,
        "organization": task.organization,
        "skill_name": task.skill_name,
        "skill_revision": task.skill_revision,
        "last_run": task.last_run or "",
        "next_run": task.next_run or "",
    }
    entry.update({key: value for key, value in optional.items() if value.strip()})
    entry = {
        key: _bounded(value) if isinstance(value, str) else value for key, value in entry.items()
    }
    if params := _allowed(task.params, _PARAM_KEYS):
        entry["params"] = params
    if inputs := _allowed(task.skill_inputs, _REPOSITORY_KEYS):
        entry["skill_inputs"] = inputs
    return entry


def _key_count(entry: dict[str, Any]) -> int:
    return len(entry) + sum(len(value) for value in entry.values() if isinstance(value, dict))


def build_registry_properties(
    tasks: tuple[ScheduledTask, ...], *, complete: bool
) -> dict[str, Any]:
    """Whole-store snapshot, truncated to the event's key and size budgets when necessary."""
    entries: list[dict[str, Any]] = []
    keys, size = _TASK_KEY_BUDGET, _TASK_BYTE_BUDGET
    for task in tasks:
        entry = registry_entry(task)
        keys -= _key_count(entry)
        size -= len(json.dumps(entry)) + 1
        if keys < 0 or size < 0:
            break
        entries.append(entry)
    return {
        "task_count": len(tasks),
        "task_store_complete": complete,
        "tasks_truncated": len(entries) < len(tasks),
        "tasks": entries,
    }


def report_task_registry() -> None:
    """Send the saved tasks on change, or again once the last report is stale; never raises."""
    global _last_reported
    try:
        from infrastructure.analytics.events import Event
        from infrastructure.analytics.provider import analytics_opted_out, get_analytics

        if analytics_opted_out():
            return
        snapshot = get_task_store_snapshot(lock_timeout_seconds=5)
        properties = build_registry_properties(snapshot.tasks, complete=snapshot.complete)
        digest = hashlib.sha256(json.dumps(properties, sort_keys=True).encode()).hexdigest()
        now = time.monotonic()
        with _report_lock:
            last = _last_reported
            if last is not None and last[0] == digest and now - last[1] < _REPORT_REFRESH_SECONDS:
                return
            get_analytics().capture(Event.SCHEDULED_TASKS_REGISTERED, properties)
            _last_reported = (digest, now)
    except Exception:
        logger.debug("Failed to report the scheduler task registry", exc_info=True)


def reset_reported_registry() -> None:
    """Forget the last report so the next pass sends again."""
    global _last_reported
    with _report_lock:
        _last_reported = None


__all__ = [
    "build_registry_properties",
    "registry_entry",
    "report_task_registry",
    "reset_reported_registry",
]
