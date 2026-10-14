"""APScheduler executor with durable, bounded scheduled-run dispatch."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Collection
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor as ControlThreadPoolExecutor
from copy import copy
from datetime import UTC, datetime
from functools import partial
from typing import Any

from apscheduler.executors.base import MaxInstancesReachedError, run_job
from apscheduler.executors.pool import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_RECOVERY_JOB_ID = "scheduler-claim-recovery"
_RECOVERABLE_BATCH_MIN = 1_000


def _job_for_run_time(job: Any, scheduled_run_time: datetime) -> Any:
    """Bind one fire time to a shallow copy of an APScheduler job."""
    invocation = copy(job)
    invocation.func = partial(job.func, scheduled_run_time=scheduled_run_time)
    return invocation


def _run_job_with_scheduled_time(
    job: Any,
    jobstore_alias: str,
    run_times: list[datetime],
    logger_name: str,
) -> list[Any]:
    """Run each submitted time with its own callback argument."""
    events: list[Any] = []
    for scheduled_run_time in run_times:
        invocation = _job_for_run_time(job, scheduled_run_time)
        events.extend(run_job(invocation, jobstore_alias, [scheduled_run_time], logger_name))
    return events


def _recoverable_runs(
    eligible_task_ids: Collection[str],
    *,
    limit: int,
) -> list[Any]:
    """Load durable work lazily to keep the APScheduler adapter import-light."""
    from infrastructure.scheduling.scheduler.storage import get_recoverable_runs

    return get_recoverable_runs(limit=limit, eligible_task_ids=eligible_task_ids)


def _recoverable_scope_runs(
    eligible_task_ids: Collection[str],
    exact_run_keys: Collection[tuple[str, str]],
    *,
    limit: int,
) -> list[Any]:
    """Load recurring IDs plus exact one-shot keys with filtering before LIMIT."""
    from infrastructure.scheduling.scheduler.storage.recoverable_keys import (
        get_recoverable_runs_for_scope,
    )

    return get_recoverable_runs_for_scope(
        eligible_task_ids,
        exact_run_keys,
        limit=limit,
    )


def _enabled_task_ids() -> set[str]:
    """Load the task store once and return every currently enabled task id."""
    from infrastructure.scheduling.scheduler import runner

    return {task.id for task in runner.list_tasks() if task.enabled}


def _durable_fire_time(scheduled_run_time: datetime) -> str:
    """Return the exact durable key used by the production admission hook."""
    return scheduled_run_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _durable_run_is_terminal(task_id: str, fire_time: str) -> bool:
    """Return whether an exact durable run can no longer require recovery."""
    from infrastructure.scheduling.scheduler.storage import get_latest_run_for_fire_time
    from infrastructure.scheduling.scheduler.types import TaskStatus

    persisted = get_latest_run_for_fire_time(task_id, fire_time)
    return persisted is not None and persisted.status in {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.SKIPPED,
        TaskStatus.ABANDONED,
    }


def _execute_recoverable_run(run: Any, runners: Any) -> list[Any]:
    """Execute one durable candidate without rewriting its persisted fire-time key."""
    from infrastructure.scheduling.scheduler import runner
    from infrastructure.scheduling.scheduler.storage import (
        get_latest_run_for_fire_time,
        record_task_success,
    )
    from infrastructure.scheduling.scheduler.types import TaskStatus

    task = runner.get_task(run.task_id)
    if task is None or not task.enabled:
        return []

    result = runner.execute_task(task, run.fire_time, runners)
    if result:
        persisted = get_latest_run_for_fire_time(task.id, run.fire_time)
        if (
            persisted is not None
            and persisted.status is TaskStatus.SUCCESS
            and persisted.work_outcome.completed
            and all(outcome.ok for outcome in persisted.targets)
        ):
            record_task_success(task.id)
    logger.info(
        "Dispatched durable task %s fire_time=%s result=%s",
        run.task_id,
        run.fire_time,
        result,
    )
    return []


def _backlog_snapshot(eligible_task_ids: Collection[str]) -> Any:
    from infrastructure.scheduling.scheduler.storage import get_backlog_snapshot

    return get_backlog_snapshot(eligible_task_ids=eligible_task_ids)


def _record_backlog_state(
    state: str,
    *,
    task_count: int,
    pending_count: int,
    oldest_pending_age_seconds: float | None,
    active_runs: int,
    capacity: int,
) -> None:
    """Emit queue pressure without recording prompts or message content."""
    from infrastructure.scheduling.scheduler.operation_log import (
        record_scheduler_service_operation,
    )

    record_scheduler_service_operation(
        "scheduler_backlog_state_changed",
        task_count=task_count,
        extra={
            "state": state,
            "pending_count": pending_count,
            "oldest_pending_age_seconds": oldest_pending_age_seconds,
            "active_runs": active_runs,
            "capacity": capacity,
        },
    )


def _is_one_shot_job(job: Any) -> bool:
    """Return whether APScheduler may remove this job immediately after submission.

    Durable recurring scheduler tasks remain represented by their registered job,
    which is also how filtered scheduler ownership is enforced after resync. Date
    jobs are different: APScheduler removes them after their only fire, so an
    admitted-but-not-yet-dispatched date tick needs a short-lived ownership hint.
    """
    trigger = getattr(job, "trigger", None)
    return trigger is not None and trigger.__class__.__name__ == "DateTrigger"


class ScheduledThreadPoolExecutor(ThreadPoolExecutor):
    """Persist cron admissions and keep user execution strictly in-memory bounded.

    In production ``on_submit`` durably records every admitted tick before this
    executor decides whether it has execution capacity. At most ``max_workers``
    user futures are then submitted to the underlying pool. Overflow remains in
    SQLite and is pulled in admission order when a slot opens.

    Recovery is a control-plane wake-up, not another user callback. It runs on a
    separate one-thread control executor so saturated user work cannot starve the
    mechanism that notices and drains durable backlog.
    """

    def __init__(
        self,
        max_workers: int = 10,
        *,
        on_submit: Callable[[str, datetime], None] | None = None,
        durable_on_submit: bool = True,
    ) -> None:
        self._on_submit = on_submit
        self._durable_on_submit = durable_on_submit
        self._max_user_workers = max_workers
        self._dispatch_lock = threading.RLock()
        self._pump_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._control_pool = ControlThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="scheduler-control",
        )
        self._control_running = False
        self._control_requested = False
        self._stopped = False
        self._shutdown_event = threading.Event()
        self._durable_dispatch = False
        self._in_memory_user_callbacks = 0
        self._peak_in_memory_user_callbacks = 0
        self._active_task_ids: set[str] = set()
        self._deferred_task_ids: set[str] = set()
        self._one_shot_run_keys: set[tuple[str, str]] = set()
        self._runners: Any | None = None
        self._last_backlog_state: str | None = None
        self._control_cycles = 0
        super().__init__(max_workers=max_workers)

    @property
    def in_memory_user_callbacks(self) -> int:
        """Current number of user futures submitted to the worker pool."""
        with self._dispatch_lock:
            return self._in_memory_user_callbacks

    @property
    def peak_in_memory_user_callbacks(self) -> int:
        """Peak user futures held by this executor since startup."""
        with self._dispatch_lock:
            return self._peak_in_memory_user_callbacks

    @property
    def control_cycles(self) -> int:
        """Completed or active durable-drain control cycles (test/benchmark signal)."""
        with self._control_lock:
            return self._control_cycles

    def start(self, scheduler: Any, alias: str) -> None:
        super().start(scheduler, alias)
        self._durable_dispatch = (
            self._on_submit is not None
            and callable(getattr(scheduler, "get_jobs", None))
            and callable(getattr(scheduler, "get_job", None))
        )

    def submit_job(self, job: Any, run_times: list[datetime]) -> None:
        """Persist first, then keep dispatch and telemetry off admission."""
        if not self._durable_dispatch:
            super().submit_job(job, run_times)
            return

        if job.id == _RECOVERY_JOB_ID:
            with self._dispatch_lock:
                self._deferred_task_ids.clear()
            self._request_drain()
            return

        if self._on_submit is not None:
            for scheduled_run_time in run_times:
                self._on_submit(job.id, scheduled_run_time)

        with self._dispatch_lock:
            if self._durable_on_submit and _is_one_shot_job(job):
                self._one_shot_run_keys.update(
                    (job.id, _durable_fire_time(scheduled_run_time))
                    for scheduled_run_time in run_times
                )
            if len(job.args) >= 2:
                self._runners = job.args[1]

        # Preserve APScheduler's per-job overlap signal after durable admission.
        # Non-durable compatibility submissions do not need a recovery wake.
        with self._lock:
            if self._instances[job.id] >= job.max_instances:
                if self._durable_on_submit:
                    self._request_drain()
                raise MaxInstancesReachedError(job)

        # Compatibility hooks cannot retain overflow durably. Reject admission
        # explicitly instead of accepting a tick that can never be replayed.
        if not self._durable_on_submit:
            if not self._submit_direct_fallback(job, run_times):
                raise MaxInstancesReachedError(job)
            return

        # Recovery queries and backlog telemetry may scan historical rows until
        # M2 indexes are integrated. The control lane owns both; admission only
        # persists the tick and schedules this bounded wake-up.
        self._request_drain()

    def _do_submit_job(self, job: Any, run_times: list[datetime]) -> None:
        """Legacy path used when durable dispatch is unavailable."""
        if self._on_submit is not None:
            for scheduled_run_time in run_times:
                self._on_submit(job.id, scheduled_run_time)

        def callback(future: Future[list[Any]]) -> None:
            exc, traceback = (
                future.exception_info()
                if hasattr(future, "exception_info")
                else (future.exception(), getattr(future.exception(), "__traceback__", None))
            )
            if exc:
                self._run_job_error(job.id, exc, traceback)
            else:
                self._run_job_success(job.id, future.result())

        future = self._pool.submit(
            _run_job_with_scheduled_time,
            job,
            job._jobstore_alias,
            run_times,
            self._logger.name,
        )
        future.add_done_callback(callback)

    def _request_drain(self) -> None:
        """Coalesce wake-ups onto the independent scheduler control lane."""
        if self._shutdown_event.is_set():
            return
        with self._control_lock:
            if self._stopped or self._shutdown_event.is_set():
                return
            self._control_requested = True
            if self._control_running:
                return
            self._control_running = True
            try:
                self._control_pool.submit(self._control_loop)
            except RuntimeError:
                self._control_running = False

    def _control_loop(self) -> None:
        """Drain all coalesced wake-ups without occupying a user worker."""
        while True:
            with self._control_lock:
                if self._stopped or self._shutdown_event.is_set():
                    self._control_running = False
                    return
                if not self._control_requested:
                    self._control_running = False
                    return
                self._control_requested = False
                self._control_cycles += 1
            try:
                self._fill_capacity()
            except Exception:  # noqa: BLE001 - control wake must survive storage failures
                logger.warning("Scheduled durable dispatch cycle failed", exc_info=True)

    def _fill_capacity(self) -> None:
        """Fill free user slots from durable work in admission order.

        The durable query is repeated after each successful reservation with the
        now-active task ids excluded from eligibility. Exact one-shot ownership
        keys are filtered in SQL before the batch limit, so stale rows for a moved
        task cannot hide the admitted DateTrigger tick or unrelated recurring work.
        """
        with self._pump_lock:
            if self._shutdown_event.is_set():
                return
            if self.in_memory_user_callbacks >= self._max_user_workers:
                self._emit_state("saturated")
                return

            recurring_task_ids, one_shot_run_keys = self._eligible_dispatch_scope()
            if self._shutdown_event.is_set():
                return
            dispatched = 0
            limit = max(_RECOVERABLE_BATCH_MIN, self._max_user_workers * 32)

            while self.in_memory_user_callbacks < self._max_user_workers:
                if self._shutdown_event.is_set():
                    return
                with self._dispatch_lock:
                    unavailable_task_ids = self._active_task_ids | self._deferred_task_ids
                    available_task_ids = recurring_task_ids - unavailable_task_ids
                    available_one_shot_keys = {
                        run_key
                        for run_key in one_shot_run_keys
                        if run_key[0] not in unavailable_task_ids
                    }
                if not available_task_ids and not available_one_shot_keys:
                    break

                if available_one_shot_keys:
                    candidates = _recoverable_scope_runs(
                        available_task_ids,
                        available_one_shot_keys,
                        limit=limit,
                    )
                else:
                    candidates = _recoverable_runs(available_task_ids, limit=limit)
                if not candidates:
                    break

                submitted_this_pass = False
                for run in candidates:
                    if self._shutdown_event.is_set():
                        return
                    if self.in_memory_user_callbacks >= self._max_user_workers:
                        break
                    if self._submit_recoverable(run):
                        dispatched += 1
                        submitted_this_pass = True

                # If the query returned rows but none could reserve a slot, do
                # not spin. A later completion/recovery wake will retry.
                if not submitted_this_pass:
                    break

            if self._shutdown_event.is_set():
                return
            if self.in_memory_user_callbacks >= self._max_user_workers:
                self._emit_state("saturated")
            elif dispatched:
                self._emit_state("draining")
            else:
                self._emit_state("idle")

    def _eligible_dispatch_scope(self) -> tuple[set[str], set[tuple[str, str]]]:
        """Return enabled recurring IDs and exact one-shot keys owned here."""
        if self._shutdown_event.is_set():
            return set(), set()

        scheduler_ids: set[str] = set()
        try:
            for job in self._scheduler.get_jobs():
                if job.id != _RECOVERY_JOB_ID:
                    scheduler_ids.add(str(job.id))
        except Exception:  # noqa: BLE001 - a concurrent resync may mutate job state
            logger.debug("Could not snapshot registered scheduler jobs", exc_info=True)

        if self._shutdown_event.is_set():
            return set(), set()

        with self._dispatch_lock:
            one_shot_run_keys = set(self._one_shot_run_keys)

        try:
            enabled_task_ids = _enabled_task_ids()
        except Exception:  # noqa: BLE001 - task-store failure is retried on the next wake
            logger.warning("Could not snapshot scheduled tasks for dispatch", exc_info=True)
            return set(), set()

        recurring_task_ids = scheduler_ids & enabled_task_ids
        exact_one_shot_keys = {
            run_key for run_key in one_shot_run_keys if run_key[0] in enabled_task_ids
        }
        return recurring_task_ids, exact_one_shot_keys

    def _eligible_task_ids(self) -> set[str]:
        """Return enabled task IDs represented by this scheduler's durable scope."""
        recurring_task_ids, one_shot_run_keys = self._eligible_dispatch_scope()
        return recurring_task_ids | {task_id for task_id, _fire_time in one_shot_run_keys}

    def _submit_recoverable(self, run: Any) -> bool:
        """Reserve one user slot and execute an exact persisted fire-time key."""
        if self._shutdown_event.is_set():
            return False

        run_key = (str(run.task_id), str(run.fire_time))
        with self._dispatch_lock:
            if (
                self._shutdown_event.is_set()
                or self._in_memory_user_callbacks >= self._max_user_workers
                or run.task_id in self._active_task_ids
                or run.task_id in self._deferred_task_ids
            ):
                return False

            is_one_shot = run_key in self._one_shot_run_keys
            registered_job: Any | None = None
            if not is_one_shot:
                try:
                    registered_job = self._scheduler.get_job(run.task_id)
                except Exception:  # noqa: BLE001 - ownership is retried on the next wake
                    logger.debug(
                        "Could not revalidate scheduler ownership for %s",
                        run.task_id,
                        exc_info=True,
                    )
                    return False
                if registered_job is None or self._shutdown_event.is_set():
                    return False

            runners = self._runners
            if runners is None and registered_job is not None and len(registered_job.args) >= 2:
                runners = registered_job.args[1]
                self._runners = runners
            if runners is None:
                runners = self._runners_from_registered_job(run.task_id)
            if runners is None or self._shutdown_event.is_set():
                return False

            max_instances = int(registered_job.max_instances) if registered_job is not None else 1
            with self._lock:
                if self._instances[run.task_id] >= max_instances:
                    return False
                self._instances[run.task_id] += 1
            self._reserve_user_slot(run.task_id)

        try:
            future = self._pool.submit(_execute_recoverable_run, run, runners)
        except Exception:
            self._rollback_reservation(run.task_id)
            raise
        future.add_done_callback(partial(self._durable_future_done, run))
        return True

    def _submit_direct_fallback(self, job: Any, run_times: list[datetime]) -> bool:
        """Execute a non-durable custom/test submission while retaining the hard bound."""
        with self._dispatch_lock:
            if (
                self._in_memory_user_callbacks >= self._max_user_workers
                or job.id in self._active_task_ids
            ):
                return False
            with self._lock:
                if self._instances[job.id] >= job.max_instances:
                    return False
                self._instances[job.id] += 1
            self._reserve_user_slot(job.id)

        try:
            future = self._pool.submit(
                _run_job_with_scheduled_time,
                job,
                job._jobstore_alias,
                run_times,
                self._logger.name,
            )
        except Exception:
            self._rollback_reservation(job.id)
            raise
        future.add_done_callback(partial(self._user_future_done, job.id))
        return True

    def _reserve_user_slot(self, task_id: str) -> None:
        self._in_memory_user_callbacks += 1
        self._peak_in_memory_user_callbacks = max(
            self._peak_in_memory_user_callbacks,
            self._in_memory_user_callbacks,
        )
        self._active_task_ids.add(task_id)

    def _rollback_reservation(self, task_id: str) -> None:
        with self._lock:
            self._instances[task_id] -= 1
        with self._dispatch_lock:
            self._active_task_ids.discard(task_id)
            self._in_memory_user_callbacks = max(0, self._in_memory_user_callbacks - 1)

    def _durable_future_done(self, run: Any, future: Future[list[Any]]) -> None:
        """Release a durable slot while deferring only the failed task itself."""
        exc, traceback = (
            future.exception_info()
            if hasattr(future, "exception_info")
            else (future.exception(), getattr(future.exception(), "__traceback__", None))
        )
        if exc:
            with self._dispatch_lock:
                self._deferred_task_ids.add(run.task_id)
            self._run_job_error(run.task_id, exc, traceback)
        else:
            self._run_job_success(run.task_id, future.result())

        with self._dispatch_lock:
            self._active_task_ids.discard(run.task_id)
            self._in_memory_user_callbacks = max(0, self._in_memory_user_callbacks - 1)

        self._retire_one_shot_key_if_terminal(run)
        # Always refill the released slot. A failed task is excluded by the
        # deferred set until periodic recovery, so unrelated pending work can
        # make immediate progress without hot-looping the failing row.
        self._request_drain()

    def _user_future_done(self, task_id: str, future: Future[list[Any]]) -> None:
        exc, traceback = (
            future.exception_info()
            if hasattr(future, "exception_info")
            else (future.exception(), getattr(future.exception(), "__traceback__", None))
        )
        if exc:
            self._run_job_error(task_id, exc, traceback)
        else:
            self._run_job_success(task_id, future.result())

        with self._dispatch_lock:
            self._active_task_ids.discard(task_id)
            self._in_memory_user_callbacks = max(0, self._in_memory_user_callbacks - 1)
        self._request_drain()

    def _retire_one_shot_key_if_terminal(self, run: Any) -> None:
        run_key = (str(run.task_id), str(run.fire_time))
        with self._dispatch_lock:
            if run_key not in self._one_shot_run_keys:
                return
        try:
            is_terminal = _durable_run_is_terminal(*run_key)
        except Exception:  # noqa: BLE001 - exact-key ownership remains safely scoped
            logger.debug(
                "Could not verify terminal one-shot run %s fire_time=%s",
                run.task_id,
                run.fire_time,
                exc_info=True,
            )
            return
        if is_terminal:
            with self._dispatch_lock:
                self._one_shot_run_keys.discard(run_key)

    def _runners_from_registered_job(self, task_id: str) -> Any | None:
        try:
            job = self._scheduler.get_job(task_id)
        except Exception:  # noqa: BLE001
            return None
        if job is None or len(job.args) < 2:
            return None
        runners = job.args[1]
        with self._dispatch_lock:
            self._runners = runners
        return runners

    def _max_instances_for_task(self, task_id: str) -> int:
        try:
            job = self._scheduler.get_job(task_id)
        except Exception:  # noqa: BLE001
            job = None
        return int(job.max_instances) if job is not None else 1

    def _emit_state(self, state: str) -> None:
        """Record only backlog state transitions, not every count mutation."""
        if self._shutdown_event.is_set():
            return
        with self._dispatch_lock:
            if state == self._last_backlog_state:
                return
            active_runs = self._in_memory_user_callbacks

        eligible_task_ids = self._eligible_task_ids()
        if self._shutdown_event.is_set():
            return
        if not eligible_task_ids and state != "idle":
            return
        try:
            snapshot = _backlog_snapshot(eligible_task_ids)
            _record_backlog_state(
                state,
                task_count=len(eligible_task_ids),
                pending_count=snapshot.pending_count,
                oldest_pending_age_seconds=snapshot.oldest_pending_age_seconds,
                active_runs=active_runs,
                capacity=self._max_user_workers,
            )
        except Exception:  # noqa: BLE001 - observability must never stop dispatch
            logger.debug("Could not emit scheduler backlog state", exc_info=True)
            return
        with self._dispatch_lock:
            self._last_backlog_state = state

    def shutdown(self, wait: bool = True) -> None:
        self._shutdown_event.set()
        with self._control_lock:
            self._stopped = True
            self._control_requested = False

        # A running control cycle can be blocked in scheduler.get_jobs/get_job
        # behind APScheduler's job-store lock while shutdown is in progress.
        # Joining that internal lane here can therefore deadlock scheduler
        # shutdown. Cancel queued wakes and let at most one in-flight cycle
        # observe the shutdown event and unwind once the scheduler lock clears.
        self._control_pool.shutdown(wait=False, cancel_futures=True)
        super().shutdown(wait=wait)


__all__ = ["ScheduledThreadPoolExecutor"]
