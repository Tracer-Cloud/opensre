"""APScheduler-backed blocking runner for scheduled tasks.

Loads all enabled tasks from the store, creates APScheduler jobs, and
blocks until SIGINT/SIGTERM. Fire times for dedup are passed directly from the
APScheduler executor to each callback (UTC, second precision), not recovered
from listener timing or wall-clock time inside the callback.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from config.constants.ci_repair import CI_REPAIR_REPORT_BUILDER
from config.constants.scheduler import SCHEDULER_MISSED_FIRE_GRACE_SECONDS
from config.constants.turn_concurrency import (
    DEFAULT_SCHEDULED_RUN_CONCURRENCY,
    OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV,
)
from config.constants.work_items import WORK_ITEM_REMINDER_RUN_AT_PARAM
from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.loop_constants import LOOP_REPORT_PARAM
from infrastructure.scheduling.scheduler.operation_log import (
    record_scheduler_execution_operation,
    record_scheduler_service_operation,
    record_scheduler_task_operation,
)
from infrastructure.scheduling.scheduler.registry_telemetry import report_task_registry
from infrastructure.scheduling.scheduler.reload_signal import (
    RELOAD_POLL_SECONDS,
    watch_and_reconcile,
)
from infrastructure.scheduling.scheduler.runners import SchedulerRunners
from infrastructure.scheduling.scheduler.storage import (
    complete_run,
    default_task_store_path,
    get_latest_run_for_fire_time,
    get_recoverable_runs,
    get_task,
    list_tasks,
    record_task_success,
    try_claim,
    try_queue_run,
    update_task,
)
from infrastructure.scheduling.scheduler.types import (
    Provider,
    ScheduledTask,
    TaskKind,
    TaskReport,
    TaskRun,
    TaskStatus,
)

logger = logging.getLogger(__name__)
TaskFilter = Callable[[ScheduledTask], bool]

_RECOVERY_JOB_ID = "scheduler-claim-recovery"
_RECOVERY_INTERVAL_SECONDS = 60


def _make_trigger(task: ScheduledTask) -> Any:
    """Build an APScheduler trigger from a task's schedule and timezone.

    Raises ValueError if the cron expression or timezone is invalid.
    """
    if task.kind is TaskKind.WORK_ITEM_REMINDER:
        run_at = task.params.get(WORK_ITEM_REMINDER_RUN_AT_PARAM, "").strip()
        if run_at:
            from apscheduler.triggers.date import DateTrigger

            try:
                run_date = datetime.fromisoformat(run_at)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid work-item reminder run_at for task {task.id}: {run_at!r}"
                ) from exc
            if run_date.tzinfo is None:
                raise ValueError(
                    f"Invalid work-item reminder run_at for task {task.id}: timezone missing"
                )
            return DateTrigger(run_date=run_date)

    try:
        return build_cron_trigger(task.cron, task.timezone)
    except ValueError as exc:
        raise ValueError(f"Invalid cron/timezone for task {task.id}: {exc}") from exc


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
    """Return the task's next UTC fire time, or raise for an invalid schedule."""
    return _next_run_from_trigger(_make_trigger(task), now)


def _compute_fire_time(scheduled_run_time: datetime) -> str:
    """Compute a stable, UTC-normalized fire_time string.

    Always converts to UTC so DST transitions don't produce ambiguous keys.
    Seconds are kept so a six-field cron firing several times a minute gets one
    claim key per tick instead of deduplicating its later ticks away.
    """
    utc_time: datetime = scheduled_run_time.astimezone(UTC)
    return utc_time.strftime("%Y-%m-%dT%H:%M:%SZ")


def _queue_scheduled_run(job_id: str, scheduled_run_time: datetime) -> None:
    """Persist an admitted task tick before APScheduler submits its worker."""
    if job_id == _RECOVERY_JOB_ID:
        return
    try_queue_run(job_id, _compute_fire_time(scheduled_run_time))


def configured_scheduled_run_limit() -> int:
    """Return the positive scheduled-worker limit, defaulting to two."""
    raw = os.getenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV)
    if raw is None:
        return DEFAULT_SCHEDULED_RUN_CONCURRENCY
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit >= 1:
        return limit
    logger.warning(
        "Ignoring %s=%r: not a positive integer; using %d.",
        OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV,
        raw,
        DEFAULT_SCHEDULED_RUN_CONCURRENCY,
    )
    return DEFAULT_SCHEDULED_RUN_CONCURRENCY


def _build_scheduler(scheduler_type: type[Any]) -> Any:
    """Build an APScheduler with the bounded scheduled-run contract."""
    from infrastructure.scheduling.scheduler.apscheduler_executor import (
        ScheduledThreadPoolExecutor,
    )

    limit = configured_scheduled_run_limit()
    return scheduler_type(
        executors={
            "default": ScheduledThreadPoolExecutor(
                max_workers=limit,
                on_submit=_queue_scheduled_run,
            )
        },
        job_defaults={"max_instances": 1},
    )


def _scheduled_job(
    task_id: str,
    runners: SchedulerRunners,
    *,
    scheduled_run_time: datetime | None = None,
) -> None:
    """Job callback invoked by APScheduler on each cron tick."""
    if scheduled_run_time is None:
        raise RuntimeError("scheduled_run_time must be supplied by the scheduler executor")
    fire_time = _compute_fire_time(scheduled_run_time)

    task = get_task(task_id)
    if task is None:
        claim = try_claim(task_id, fire_time)
        if claim is None:
            return
        logger.warning("Task %s not found in store, skipping", task_id)
        record_scheduler_service_operation(
            "scheduler_job_skipped",
            extra={"task_id": task_id, "fire_time": fire_time, "reason": "missing_task"},
        )
        complete_run(claim, status=TaskStatus.SKIPPED, error="missing_task")
        return
    if not task.enabled:
        claim = try_claim(task_id, fire_time)
        if claim is None:
            return
        logger.info("Task %s is disabled, skipping", task_id)
        record_scheduler_execution_operation(
            "scheduled_task_execution_skipped",
            task,
            fire_time=fire_time,
            status=TaskStatus.SKIPPED,
            extra={"reason": "disabled"},
        )
        complete_run(claim, status=TaskStatus.SKIPPED, error="disabled")
        return

    result = execute_task(task, fire_time, runners)

    if result:
        _record_task_success_after_full_delivery(task.id, fire_time)


def _complete_recoverable_as_skipped(
    run: Any,
    task: ScheduledTask | None,
) -> bool:
    """Drop a queued tick whose schedule was disabled or deleted."""
    claim = try_claim(run.task_id, run.fire_time)
    if claim is None:
        return False
    reason = "missing_task" if task is None else "disabled"
    if task is None:
        record_scheduler_service_operation(
            "scheduler_job_skipped",
            extra={"task_id": run.task_id, "fire_time": run.fire_time, "reason": reason},
        )
    else:
        record_scheduler_execution_operation(
            "scheduled_task_execution_skipped",
            task,
            fire_time=run.fire_time,
            status=TaskStatus.SKIPPED,
            extra={"reason": reason},
        )
    complete_run(claim, status=TaskStatus.SKIPPED, error=reason)
    return True


def _skip_cancelled_recoverable_runs() -> None:
    """Finish disabled/deleted ticks without occupying the live-recovery scan."""
    while True:
        skipped = 0
        for run in get_recoverable_runs():
            task = get_task(run.task_id)
            if task is not None and task.enabled:
                continue
            if _complete_recoverable_as_skipped(run, task):
                skipped += 1
        if skipped == 0:
            return


def _recover_runs(
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
    scheduled_run_time: datetime | None = None,
) -> None:
    """Resume pending and expired ticks within the scheduler worker pool."""
    _ = scheduled_run_time
    eligible_task_ids = _desired_task_ids(task_filter=task_filter)
    # Cancelled ticks must be skip-completed even when they outnumber the
    # recovery scan limit, or they stay queued and fire after a later re-enable.
    _skip_cancelled_recoverable_runs()
    for run in get_recoverable_runs(eligible_task_ids=eligible_task_ids):
        task = get_task(run.task_id)
        if task is None or not task.enabled:
            _complete_recoverable_as_skipped(run, task)
            continue
        result = execute_task(task, run.fire_time, runners)
        if result:
            _record_task_success_after_full_delivery(task.id, run.fire_time)
        logger.info(
            "Recovered task %s fire_time=%s result=%s",
            run.task_id,
            run.fire_time,
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
        _recover_runs,
        trigger=IntervalTrigger(seconds=_RECOVERY_INTERVAL_SECONDS),
        args=[runners],
        kwargs={"task_filter": task_filter},
        id=_RECOVERY_JOB_ID,
        name="scheduler:expired-claim-recovery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=None,
        next_run_time=datetime.now(UTC),
    )


def _stored_time(raw: str | None) -> datetime | None:
    """A stored ISO timestamp in UTC; a naive one is UTC, an unreadable one ``None``."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _immediate_ci_repair_fire(task: ScheduledTask, now: datetime) -> datetime | None:
    """Due time for a never-run CI repair whose stored next run is already due."""
    if task.last_run is not None:
        return None
    if task.params.get(LOOP_REPORT_PARAM) != CI_REPAIR_REPORT_BUILDER:
        return None
    due = _stored_time(task.next_run)
    if due is None or due > now:
        return None
    return due


def _missed_fire(task: ScheduledTask, trigger: Any, now: datetime) -> datetime | None:
    """The latest fire no scheduler ran, when it falls inside the grace window.

    A replaced hosted gateway runs no scheduler for minutes, and a starting one
    resumes at the first fire after now, so a tick due in that gap never runs.
    Only fires from the stored next run on count: that is the first fire the
    last registration expected, so an earlier slot predates the task or its
    enabling. A fire with a run record was queued by a scheduler, which ran it
    or left it to the recovery sweep. Several missed fires coalesce into the
    latest, and its fire time is the claim key, so it runs at most once.
    """
    expected = _stored_time(task.next_run)
    if expected is None or expected > now:
        return None
    window_start = max(expected, now - timedelta(seconds=SCHEDULER_MISSED_FIRE_GRACE_SECONDS))
    missed: datetime | None = None
    fire = cast(datetime | None, trigger.get_next_fire_time(None, window_start))
    while fire is not None and window_start <= fire <= now:
        missed = fire
        fire = cast(datetime | None, trigger.get_next_fire_time(fire, fire))
    if missed is None:
        return None
    fire_time = _compute_fire_time(missed)
    try:
        recorded = get_latest_run_for_fire_time(task.id, fire_time)
    except (OSError, sqlite3.Error):
        logger.warning(
            "Not catching up task %s fire_time=%s: run history is unreadable", task.id, fire_time
        )
        return None
    return missed if recorded is None else None


def _register_jobs(
    scheduler: Any,
    runners: SchedulerRunners,
    *,
    task_filter: TaskFilter | None = None,
    catch_up: bool = False,
) -> int:
    """Register all enabled tasks on *scheduler*; invalid tasks are logged and skipped.

    ``catch_up`` is for a starting scheduler: it also fires, once, the latest
    tick missed while no scheduler ran (see :func:`_missed_fire`). A live resync
    never does, so editing a schedule cannot fire one of its past slots.
    """
    enabled_count = 0
    now = datetime.now(UTC)
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
        immediate = _immediate_ci_repair_fire(task, now)
        missed: datetime | None = None
        if immediate is None and catch_up:
            immediate = missed = _missed_fire(task, trigger, now)
        job_kwargs: dict[str, Any] = {}
        next_run: str | None
        if immediate is not None:
            # Fire the due time now and keep the stored next run: the trigger
            # alone resumes at its next slot, and storing that slot would hide
            # this fire from a re-registration before it runs.
            next_run = immediate.isoformat()
            job_kwargs["next_run_time"] = immediate
        else:
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
            misfire_grace_time=None,
            max_instances=1,
            **job_kwargs,
        )
        enabled_count += 1
        registration: dict[str, Any] = {"next_run": next_run}
        if missed is not None:
            registration["missed_fire_time"] = _compute_fire_time(missed)
            logger.info(
                "Catching up task %s fire_time=%s, missed while no scheduler ran",
                task.id,
                registration["missed_fire_time"],
            )
        record_scheduler_task_operation(
            "scheduler_job_registered",
            task,
            extra=registration,
        )
        logger.info(
            "Registered task %s (%s) with cron=%s tz=%s",
            task.id,
            task.kind,
            task.cron,
            task.timezone,
        )
    if task_filter is None:
        # Only a host that runs the whole store can report it; a filtered shell
        # scheduler registers a subset.
        report_task_registry()
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
    enabled tasks. The caller owns shutdown via ``scheduler.shutdown()``. A
    tick missed while no scheduler ran fires once at start.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = _build_scheduler(BackgroundScheduler)
    enabled_count = _register_jobs(scheduler, runners, task_filter=task_filter, catch_up=True)
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
    from the reload signal without a restart. A tick missed while no scheduler
    ran fires once at start.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = _build_scheduler(BlockingScheduler)
    enabled_count = _register_jobs(scheduler, runners, catch_up=True)
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


def run_task_now(
    task_id: str,
    runners: SchedulerRunners,
    *,
    only_failed: bool = False,
    on_result: Callable[[TaskRun], None] | None = None,
) -> bool:
    """Execute a task immediately (ad-hoc one-shot for debugging).

    Uses the current time with microsecond precision as fire_time so it does
    not conflict with scheduled runs (which use second precision).

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
    replay_report: TaskReport | None = None
    if only_failed:
        target_filter = failed_retry_scope(task_id)
        if target_filter is None:
            logger.warning(
                "Task %s has no readable per-target history; refusing to widen "
                "a failed-only retry to every destination",
                task_id,
            )
            return False

        if not target_filter:
            return True

        from infrastructure.scheduling.scheduler.storage import get_latest_targeted_run

        previous = get_latest_targeted_run(task_id)
        replay_report = previous.retained_report() if previous is not None else None
        if replay_report is None:
            logger.warning(
                "Task %s has no retained report; refusing to repeat work for delivery.", task_id
            )
            return False

    fire_time = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result = execute_task(
        task,
        fire_time,
        runners,
        target_filter=target_filter,
        replay_report=replay_report,
        on_result=on_result,
    )
    if result:
        _record_task_success_after_full_delivery(task.id, fire_time)
    return result


def _record_task_success_after_full_delivery(task_id: str, fire_time: str) -> None:
    """Finalize a task only when its persisted run completed every target."""
    run = get_latest_run_for_fire_time(task_id, fire_time)
    if (
        run is not None
        and run.status is TaskStatus.SUCCESS
        and run.work_outcome.completed
        and all(outcome.ok for outcome in run.targets)
    ):
        record_task_success(task_id)


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
    "configured_scheduled_run_limit",
    "compute_next_run",
    "failed_retry_scope",
    "refresh_background_scheduler",
    "resync_scheduler_jobs",
    "run_task_now",
    "start_background_scheduler",
    "start_scheduler",
]
