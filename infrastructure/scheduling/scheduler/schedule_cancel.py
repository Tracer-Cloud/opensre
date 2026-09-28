"""Cancel a scheduled tick when the stored task is gone or disabled.

``/loops stop``, ``opensre cron remove``, and work-item completion all mutate
the task store. In-flight APScheduler workers still hold the pre-mutation
snapshot, so the executor and scheduled agent turns re-read this helper
instead of finishing work the user already cancelled.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from infrastructure.scheduling.scheduler.storage import get_task
from infrastructure.scheduling.scheduler.storage import task_store as _task_store


def schedule_cancelled(task_id: str) -> bool:
    """True when ``task_id`` is missing from the store or disabled."""
    return schedule_cancel_reason(task_id) is not None


def schedule_cancel_reason(task_id: str) -> str | None:
    """``missing_task`` / ``disabled``, or ``None`` when the schedule is still live.

    An in-memory task that was never written to the store is not cancelled: the
    store file is absent. ``cron remove`` / ``/loops delete`` leave the store
    file in place without that id, which is ``missing_task``.
    """
    task_id = task_id.strip()
    if not task_id:
        return None
    current = get_task(task_id)
    if current is not None:
        return None if current.enabled else "disabled"
    if not _task_store.default_task_store_path().exists():
        return None
    return "missing_task"


def cancel_requested_for_payload(payload: Mapping[str, object]) -> Callable[[], bool] | None:
    """ReAct cancel probe for a scheduled runner payload, or ``None`` if unscoped."""
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return None
    return lambda: schedule_cancelled(task_id)


__all__ = [
    "cancel_requested_for_payload",
    "schedule_cancel_reason",
    "schedule_cancelled",
]
