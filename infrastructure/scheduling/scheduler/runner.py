"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates CronTrigger jobs, and
blocks until SIGINT/SIGTERM. Fire times for dedup come from
``JobSubmissionEvent.scheduled_run_times[0]`` (UTC, minute precision),
not wall-clock time inside the callback.
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.operation_log import (
    record_scheduler_execution_operation,
    record_scheduler_service_operation,
    record_scheduler_task_operation,
)
from infrastructure.scheduling.scheduler.reload_signal import (
    RELOAD_POLL_SECONDS,
    watch_and_reconcile,
)
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import (
    default_task_store_path,
    get_expired_claims,
    get_task,
    list_tasks,
    record_task_success,
    update_task,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskStatus

logger = logging.getLogger(__name__)
TaskFilter = Callable[[ScheduledTask], bool]

# Populated by EVENT_JOB_SUBMITTED before each job runs (job_id -> fire_time).
_pending_fire_times: dict[str, str] = {}
_pending_fire_times_lock = threading.Lock()
_RECOVERY_JOB_ID = "scheduler-claim-recovery"
_RECOVERY_INTERVAL_SECONDS = 60


def _make_trigger(task: ScheduledTask) -> Any:
    """Build an APScheduler CronTrigger from a task's cron expression and timezone.

    Raises ValueError if the cron expression or timezone is invalid.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = task.cron.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {task.cron!r}")

    try:
        trigger = CronTrigger.from_crontab(task.cron, timezone=task.timezone)
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Invalid cron/timezone for task {task.id}: {exc}") from exc
    return trigger


def _next_run_from_trigger(trigger: Any, now: datetime | None = None) -> str | None:
    """Return the next UTC fire time for an already-built trigger."""
    base = now or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    next_fire = cast(datetime | None, trigger.get_next_fire_time(None, base))
    if next_fire is None:
        return None
    return next_fire.astimezone(UTC).isoformat()


def compute_next_run(task: ScheduledTask, now: datetime | None = None) -> str | None:
    """Return the task's next UTC cron fire time, or raise for invalid schedules."""
    return _next_run_from_trigger(_make_trigger(task), now)


def _compute_fire_time(scheduled_run_time: Any) -> str:
    """Compute a stable, UTC-normalized fire_time string.

    Always converts to UTC so DST transitions don't produce ambiguous keys.
    """
    if scheduled_run_time is not None:
        utc_time: datetime = scheduled_run_time.astimezone(UTC)
        return utc_time.strftime("%Y-%m-%dT%H:%MZ")
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _on_job_submitted(event: Any) -> None:
    """Capture the intended fire time for this tick before the job callback runs."""
    run_times = getattr(event, "scheduled_run_times", None)
    if run_times:
        fire_time = _compute_fire_time(run_times[0])
    else:
        fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    with _pending_fire_times_lock:
        _pending_fire_times[event.job_id] = fire_time


def _scheduled_job(task_id: str, runners: SchedulerRunners) -> None:
    """Job callback invoked by APScheduler on each cron tick."""
    with _pending_fire_times_lock:
        fire_time = _pending_fire_times.pop(task_id, None)
    if fire_time is None:
        logger.warning(
            "No scheduled fire_time for task %s; using UTC now (listener may have missed)",
            task_id,
        )
        fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")

    task = get_task(task_id)
    if task is None:
        logger.warning("Task %s not found in store, skipping", task_id)
        record_scheduler_service_operation(
            "scheduler_job_skipped",
            extra={"task_id": task_id, "fire_time": fire_time, "reason": "missing_task"},
        )
        return
    if not task.enabled:
        logger.info("Task %s is disabled, skipping", task_id)
        record_scheduler_execution_operation(
            "scheduled_task_execution_skipped",
            task,
            fire_time=fire_time,
            status=TaskStatus.SKIPPED,
            extra={"reason": "disabled"},
        )
        return

    result = execute_task(task, fire_time, runners)

    if result:
        record_task_success(task.id)


def _recover_expired_tasks(
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
) -> None:
    """Resubmit expired scheduled ticks through the normal fenced executor."""
    eligible_task_ids = _desired_task_ids(task_filter=task_filter)
    for expired in get_expired_claims(eligible_task_ids=eligible_task_ids):
        task = get_task(expired.task_id)
        if task is None or not task.enabled:
            continue
        if task_filter is not None and not task_filter(task):
            continue
        result = execute_task(task, expired.fire_time, runners)
        if result:
            record_task_success(task.id)
        logger.info(
            "Recovered expired task %s fire_time=%s result=%s",
            expired.task_id,
            expired.fire_time,
            result,
        )


def _register_recovery_job(
    scheduler: Any,
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
) -> None:
    """Install the periodic recovery sweep on a live APScheduler instance."""
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        _recover_expired_tasks,
        trigger=IntervalTrigger(seconds=_RECOVERY_INTERVAL_SECONDS),
        args=[runners],
        kwargs={"task_filter": task_filter},
        id=_RECOVERY_JOB_ID,
        name="scheduler:expired-claim-recovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )


def _register_jobs(
    scheduler: Any,
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
    add_listener: bool = True,
) -> int:
    """Register all enabled tasks on *scheduler*; invalid tasks are logged and skipped."""
    if add_listener:
        from apscheduler.events import EVENT_JOB_SUBMITTED

        scheduler.add_listener(_on_job_submitted, EVENT_JOB_SUBMITTED)

    enabled_count = 0
    for task in list_tasks():
        if not task.enabled:
            continue
        if task_filter is not None and not task_filter(task):
            continue
        try:
            trigger = _make_trigger(task)
        except ValueError as exc:
            logger.error("Skipping task %s: %s", task.id, exc)
            continue
        next_run = _next_run_from_trigger(trigger)
        if task.next_run != next_run:
            task.next_run = next_run
            update_task(task)

        scheduler.add_job(
            _scheduled_job,
            trigger=trigger,
            args=[task.id, runners],
            id=task.id,
            name=f"{task.kind.value}:{task.id}",
            replace_existing=True,
            misfire_grace_time=60,
        )
        enabled_count += 1
        record_scheduler_task_operation(
            "scheduler_job_registered",
            task,
            extra={"next_run": next_run},
        )
        logger.info(
            "Registered task %s (%s) with cron=%s tz=%s",
            task.id,
            task.kind,
            task.cron,
            task.timezone,
        )
    return enabled_count


def _desired_task_ids(*, task_filter: TaskFilter | None = None) -> set[str]:
    """Return enabled task ids that should be registered under ``task_filter``."""
    desired: set[str] = set()
    for task in list_tasks():
        if not task.enabled:
            continue
        if task_filter is not None and not task_filter(task):
            continue
        desired.add(task.id)
    return desired


def resync_scheduler_jobs(
    scheduler: Any,
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
) -> int:
    """Replace registered jobs on a live scheduler with the current task store."""
    existing_ids = {job.id for job in scheduler.get_jobs()}
    enabled_count = _register_jobs(
        scheduler,
        runners,
        task_filter=task_filter,
        add_listener=False,
    )
    desired_ids = _desired_task_ids(task_filter=task_filter)
    for job_id in existing_ids - desired_ids - {_RECOVERY_JOB_ID}:
        try:
            scheduler.remove_job(job_id)
            record_scheduler_service_operation(
                "scheduler_job_removed",
                task_count=enabled_count,
                extra={"task_id": job_id, "reason": "not_desired"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to remove stale scheduler job %s: %s", job_id, exc)
    if enabled_count > 0:
        _register_recovery_job(scheduler, runners, task_filter=task_filter)
    elif _RECOVERY_JOB_ID in existing_ids:
        scheduler.remove_job(_RECOVERY_JOB_ID)
    logger.info("Scheduler resynced with %d enabled task(s)", enabled_count)
    record_scheduler_service_operation("scheduler_resynced", task_count=enabled_count)
    return enabled_count


def refresh_background_scheduler(
    scheduler: Any | None,
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
) -> tuple[Any | None, int]:
    """Resync ``scheduler`` or start one when the store gained its first task.

    Returns ``(scheduler, task_count)``. When every task is disabled/removed the
    existing scheduler is shut down and ``None`` is returned.
    """
    if scheduler is None:
        return start_background_scheduler(runners, task_filter=task_filter)

    enabled_count = resync_scheduler_jobs(scheduler, runners, task_filter=task_filter)
    if enabled_count > 0:
        return scheduler, enabled_count

    try:
        scheduler.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Scheduler shutdown after empty resync failed: %s", exc)
    record_scheduler_service_operation(
        "scheduler_stopped",
        task_count=0,
        extra={"reason": "no_enabled_tasks"},
    )
    return None, 0


def start_background_scheduler(
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
) -> tuple[Any, int]:
    """Start a non-blocking scheduler for embedding in a host process.

    Installs no signal handlers and never exits the process. Returns
    ``(scheduler, task_count)``; the scheduler is ``None`` when there are no
    enabled tasks. The caller owns shutdown via ``scheduler.shutdown()``.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    enabled_count = _register_jobs(scheduler, runners, task_filter=task_filter)
    if enabled_count == 0:
        record_scheduler_service_operation("scheduler_idle", task_count=0)
        return None, 0
    _register_recovery_job(scheduler, runners, task_filter=task_filter)
    scheduler.start()
    logger.info("Scheduler started with %d task(s). Waiting for triggers...", enabled_count)
    record_scheduler_service_operation("scheduler_started", task_count=enabled_count)
    return scheduler, enabled_count


def _watch_reload_signal(
    scheduler: Any, runners: SchedulerRunners, stop_event: threading.Event
) -> None:
    """Keep the blocking scheduler's jobs in sync with the task store until stopped.

    The blocking scheduler registers tasks once, so without this a task added by
    another process (``opensre cron add``) would not run until a restart.
    Delegates to the shared watcher: reload signal (fast path) plus a store-file
    reconcile, so a dropped signal still converges on the next poll.
    """
    watch_and_reconcile(
        stop_event,
        lambda: resync_scheduler_jobs(scheduler, runners),
        default_task_store_path(),
        on_error=lambda exc: logger.warning("Scheduler resync failed; will retry: %s", exc),
    )


def start_scheduler(runners: SchedulerRunners, *, idle_when_empty: bool = False) -> None:
    """Load all enabled tasks and start the blocking scheduler.

    Blocks until SIGINT or SIGTERM. Invalid tasks (bad cron, bad timezone)
    are logged and skipped rather than crashing the entire daemon. With no
    enabled tasks the CLI exits with guidance; ``idle_when_empty`` (a dedicated
    scheduler service) idles and waits instead, so tasks can be added later
    without the process crash-looping. Tasks added while running are picked up
    from the reload signal without a restart.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    enabled_count = _register_jobs(scheduler, runners)
    if enabled_count == 0 and not idle_when_empty:
        logger.warning("No enabled tasks found. Scheduler has nothing to run.")
        record_scheduler_service_operation("scheduler_idle", task_count=0)
        raise SystemExit("No enabled tasks found. Add tasks with `opensre cron add` first.")
    if enabled_count > 0:
        _register_recovery_job(scheduler, runners)

    stop_event = threading.Event()

    def _shutdown_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown_handler)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        signal.signal(sigterm, _shutdown_handler)

    # Watch for reloads so a task added by another process (`cron add`) is picked
    # up live. The startup sentinel is deliberately NOT drained here: a task added
    # between the initial registration above and now has already written it, so
    # the watcher must consume and resync it rather than discard it.
    reload_watcher = threading.Thread(
        target=_watch_reload_signal,
        args=(scheduler, runners, stop_event),
        name="scheduler-reload-watch",
        daemon=True,
    )
    reload_watcher.start()

    if enabled_count == 0:
        logger.info("No enabled tasks yet; scheduler idle, waiting (add with `opensre cron add`).")
    else:
        logger.info("Scheduler started with %d task(s). Waiting for triggers...", enabled_count)
    record_scheduler_service_operation(
        "scheduler_started" if enabled_count else "scheduler_idle",
        task_count=enabled_count,
        extra={"blocking": True},
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
    finally:
        stop_event.set()
        reload_watcher.join(timeout=RELOAD_POLL_SECONDS + 1.0)
        record_scheduler_service_operation("scheduler_stopped", task_count=enabled_count)


def run_task_now(task_id: str, runners: SchedulerRunners, *, only_failed: bool = False) -> bool:
    """Execute a task immediately (ad-hoc one-shot for debugging).

    Uses the current time with seconds precision as fire_time so it does
    not conflict with scheduled runs (which use minute precision).

    ``only_failed=True`` retries only the destinations the most recently
    completed run failed at, instead of delivering to every configured
    destination again -- recovering a partial failure without re-posting to
    channels that already received the message.

    It never widens: when no usable per-target history can be read, the run is
    refused rather than falling back to delivering everywhere. Widening is the
    one direction that causes harm the operator did not ask for (a duplicate
    report at a destination that already received it), and a caller that does
    want every destination has one -- an ordinary run without ``only_failed``.
    """
    task = get_task(task_id)
    if task is None:
        return False

    target_filter: frozenset[tuple[Provider, str]] | None = None
    if only_failed:
        target_filter = failed_retry_scope(task_id)
        if target_filter is None:
            logger.warning(
                "Task %s has no readable per-target history; refusing to widen "
                "a failed-only retry to every destination",
                task_id,
            )
            return False

    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return execute_task(task, fire_time, runners, target_filter=target_filter)


def failed_retry_scope(task_id: str) -> frozenset[tuple[Provider, str]] | None:
    """Destinations a ``--failed-only`` retry of ``task_id`` should target.

    ``None`` means no run with readable per-target outcomes could be found, so
    what failed is unknown and the caller must not retry: there is no scope to
    narrow to, and widening to every destination would re-post where the
    message already landed. An empty (non-``None``) set means history was read
    and nothing had failed -- there is simply nothing to retry.
    """
    from infrastructure.scheduling.scheduler.storage import get_latest_targeted_run

    run = get_latest_targeted_run(task_id)
    if run is None:
        return None
    return frozenset(
        (outcome.provider, outcome.chat_id) for outcome in run.targets if not outcome.ok
    )


__all__ = [
    "compute_next_run",
    "failed_retry_scope",
    "refresh_background_scheduler",
    "resync_scheduler_jobs",
    "run_task_now",
    "start_background_scheduler",
    "start_scheduler",
]
