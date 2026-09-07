"""Interactive-shell lifecycle wrapper for prompt loop scheduling."""

from __future__ import annotations

import logging
import threading
from typing import Any

from bootstrap.process import SCHEDULED_COMMAND_PROFILE, configure_process
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.operation_log import record_scheduler_service_operation
from infrastructure.scheduling.scheduler.runner import start_background_scheduler
from infrastructure.scheduling.scheduler.types import ScheduledTask

log = logging.getLogger(__name__)

_scheduler_lock = threading.Lock()
_scheduler: Any | None = None
_task_count = 0


def _is_prompt_loop_task(task: ScheduledTask) -> bool:
    """Return whether a task is a shell-created recurring prompt loop."""
    return bool(task.params.get(LOOP_PROMPT_PARAM, "").strip())


def start_loop_scheduler() -> int:
    """Start the shell-local prompt-loop scheduler if it is not already running."""
    with _scheduler_lock:
        if _scheduler is not None:
            return _task_count
        return _start_locked()


def reload_loop_scheduler() -> int:
    """Reload prompt-loop jobs after a loop is created or changed.

    Also signals any long-lived gateway scheduler so jobs stay registered after
    this shell exits.
    """
    from infrastructure.scheduling.scheduler.reload_signal import request_scheduler_reload

    request_scheduler_reload()
    record_scheduler_service_operation(
        "loop_scheduler_reload_requested",
        task_count=_task_count,
    )
    with _scheduler_lock:
        _shutdown_locked()
        return _start_locked()


def run_loop_now(task_id: str) -> bool:
    """Fire one loop task immediately in this process; False when the run failed."""
    from bootstrap.adapters import scheduler_runners
    from infrastructure.scheduling.scheduler.runner import run_task_now

    configure_process(SCHEDULED_COMMAND_PROFILE)
    return run_task_now(task_id, scheduler_runners())


def shutdown_loop_scheduler() -> None:
    """Stop the shell-local prompt-loop scheduler."""
    with _scheduler_lock:
        _shutdown_locked()


def _start_locked() -> int:
    global _scheduler, _task_count

    from bootstrap.adapters import scheduler_runners

    configure_process(SCHEDULED_COMMAND_PROFILE)
    scheduler, task_count = start_background_scheduler(
        scheduler_runners(), task_filter=_is_prompt_loop_task
    )
    _scheduler = scheduler
    _task_count = task_count
    if scheduler is None:
        log.debug("Interactive shell prompt-loop scheduler idle; no enabled loops.")
        record_scheduler_service_operation(
            "loop_scheduler_idle",
            task_count=0,
            extra={"host": "interactive_shell"},
        )
    else:
        log.info("Interactive shell prompt-loop scheduler running %d loop(s).", task_count)
        record_scheduler_service_operation(
            "loop_scheduler_started",
            task_count=task_count,
            extra={"host": "interactive_shell"},
        )
    return task_count


def _shutdown_locked() -> None:
    global _scheduler, _task_count

    scheduler = _scheduler
    task_count = _task_count
    _scheduler = None
    _task_count = 0
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
        record_scheduler_service_operation(
            "loop_scheduler_stopped",
            task_count=task_count,
            extra={"host": "interactive_shell"},
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("Interactive shell prompt-loop scheduler shutdown failed: %s", exc)


__all__ = [
    "reload_loop_scheduler",
    "run_loop_now",
    "shutdown_loop_scheduler",
    "start_loop_scheduler",
]
