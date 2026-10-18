"""Cancel a scheduled tick when the stored task is gone or disabled.

``/loops stop``, ``opensre cron remove``, and work-item completion all mutate
the task store. In-flight APScheduler workers still hold the pre-mutation
snapshot, so the executor and scheduled agent turns re-read this helper
instead of finishing work the user already cancelled.

A run someone asked for explicitly on a paused task (:func:`running_despite_pause`)
is not cancelled by the pause; deleting the task still cancels it.
"""

from __future__ import annotations

import contextlib
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Mapping

from infrastructure.scheduling.scheduler.storage import get_task
from infrastructure.scheduling.scheduler.storage import task_store as _task_store

_explicit_runs_lock = threading.Lock()
#: Task id -> explicit runs in flight in this process that a pause must not cancel.
_explicit_runs: Counter[str] = Counter()


@contextlib.contextmanager
def running_despite_pause(task_id: str) -> Iterator[None]:
    """While held, a disabled ``task_id`` counts as live for this process's cancel checks."""
    with _explicit_runs_lock:
        _explicit_runs[task_id] += 1
    try:
        yield
    finally:
        with _explicit_runs_lock:
            _explicit_runs[task_id] -= 1
            if _explicit_runs[task_id] <= 0:
                del _explicit_runs[task_id]


def _runs_despite_pause(task_id: str) -> bool:
    with _explicit_runs_lock:
        return _explicit_runs[task_id] > 0


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
        return None if current.enabled or _runs_despite_pause(task_id) else "disabled"
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
    "running_despite_pause",
    "schedule_cancel_reason",
    "schedule_cancelled",
]
