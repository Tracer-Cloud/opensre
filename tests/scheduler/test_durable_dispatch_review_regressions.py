"""Regressions for filtered ownership, durable fallback, and backlog fairness."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.events import JobEvent
from apscheduler.executors.base import MaxInstancesReachedError

from infrastructure.scheduling.scheduler import apscheduler_executor as scheduler_executor
from infrastructure.scheduling.scheduler import runner


class _Scheduler:
    def __init__(self, jobs: list[SimpleNamespace]) -> None:
        self.jobs = {job.id: job for job in jobs}
        self.event_codes: list[int] = []

    def _create_lock(self) -> threading.RLock:
        return threading.RLock()

    def _dispatch_event(self, event: JobEvent) -> None:
        self.event_codes.append(event.code)

    def get_jobs(self) -> list[SimpleNamespace]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> SimpleNamespace | None:
        return self.jobs.get(job_id)


def _job(
    task_id: str,
    runners: object,
    *,
    callback: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        max_instances=1,
        misfire_grace_time=None,
        func=callback or (lambda **_kwargs: None),
        args=(task_id, runners),
        kwargs={},
        _jobstore_alias="default",
        trigger=SimpleNamespace(),
    )


def _silence_observability(
    monkeypatch: pytest.MonkeyPatch,
    pending: list[SimpleNamespace],
    scheduler: _Scheduler,
) -> None:
    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: set(scheduler.jobs),
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=len(pending),
            oldest_pending_age_seconds=0.0 if pending else None,
        ),
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )


def test_resync_removed_recurring_job_is_not_drained_by_old_filtered_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task removed by filtered resync remains durable for its new owner."""
    runners = object()
    first = _job("task-first", runners)
    moved = _job("task-moved", runners)
    scheduler = _Scheduler([first, moved])
    pending: list[SimpleNamespace] = []
    lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()
    moved_started = threading.Event()

    def on_submit(task_id: str, scheduled_run_time: datetime) -> None:
        with lock:
            pending.append(
                SimpleNamespace(
                    task_id=task_id,
                    fire_time=scheduled_run_time.isoformat(),
                )
            )

    def recoverable_runs(
        eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        with lock:
            return [run for run in pending if run.task_id in eligible_task_ids][:limit]

    def execute_recoverable(run: SimpleNamespace, _runners: object) -> list[object]:
        with lock:
            pending.remove(run)
        if run.task_id == first.id:
            first_started.set()
            assert release_first.wait(10)
        else:
            moved_started.set()
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    _silence_observability(monkeypatch, pending, scheduler)

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=on_submit,
    )
    executor.start(scheduler, "default")
    now = datetime.now(UTC)
    try:
        executor.submit_job(first, [now])
        assert first_started.wait(10)
        executor.submit_job(moved, [now + timedelta(seconds=1)])
        scheduler.jobs.pop(moved.id)
        release_first.set()

        assert not moved_started.wait(0.5)
        with lock:
            assert [run.task_id for run in pending] == [moved.id]
    finally:
        release_first.set()
        executor.shutdown(wait=True)


def test_ownership_is_revalidated_at_reservation_after_candidate_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resync between eligibility and reservation prevents the stale dispatch."""
    runners = object()
    moved = _job("task-moved", runners)
    scheduler = _Scheduler([moved])
    run = SimpleNamespace(
        task_id=moved.id,
        fire_time="2026-09-17T12:00:00Z",
    )
    pending = [run]
    executed = threading.Event()

    def recoverable_runs(
        eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        assert moved.id in eligible_task_ids
        scheduler.jobs.pop(moved.id, None)
        return pending[:limit]

    def execute_recoverable(
        _run: SimpleNamespace,
        _runners: object,
    ) -> list[object]:
        executed.set()
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: {moved.id},
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=1,
            oldest_pending_age_seconds=0.0,
        ),
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    try:
        executor._fill_capacity()
        assert not executed.is_set()
        assert pending == [run]
        assert executor.in_memory_user_callbacks == 0
    finally:
        executor.shutdown(wait=True)


def test_eligibility_reads_task_store_once_per_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large ownership scan uses one task-store snapshot, not one read per ID."""
    tasks = [SimpleNamespace(id=f"task-{index}", enabled=index % 2 == 0) for index in range(1_000)]
    calls = 0

    def list_tasks_once() -> list[SimpleNamespace]:
        nonlocal calls
        calls += 1
        return tasks

    monkeypatch.setattr(runner, "list_tasks", list_tasks_once)

    enabled = scheduler_executor._enabled_task_ids()

    assert calls == 1
    assert len(enabled) == 500
    assert "task-0" in enabled
    assert "task-1" not in enabled


def test_durable_disabled_tick_does_not_use_direct_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted disabled tick stays durable instead of becoming SKIPPED."""
    runners = object()
    callback_started = threading.Event()

    def callback(**_kwargs: object) -> None:
        callback_started.set()

    job = _job("task-disabled", runners, callback=callback)
    scheduler = _Scheduler([job])

    monkeypatch.setattr(scheduler_executor, "_enabled_task_ids", set)
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=1,
            oldest_pending_age_seconds=0.0,
        ),
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, [datetime.now(UTC)])
    finally:
        executor.shutdown(wait=True)

    assert not callback_started.is_set()
    assert executor.peak_in_memory_user_callbacks == 0


def test_durable_submission_scans_and_reports_only_on_control_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission only wakes recovery; scans and telemetry stay on control."""
    runners = object()
    job = _job("task-durable", runners)
    scheduler = _Scheduler([job])
    scanned = threading.Event()
    reported = threading.Event()
    scan_threads: list[str] = []
    telemetry_threads: list[str] = []

    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: {job.id},
    )

    def recoverable_runs(
        _eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        _ = limit
        scan_threads.append(threading.current_thread().name)
        scanned.set()
        return []

    def record_state(*_args: object, **_kwargs: object) -> None:
        telemetry_threads.append(threading.current_thread().name)
        reported.set()

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_record_backlog_state", record_state)
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=0,
            oldest_pending_age_seconds=None,
        ),
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, [datetime.now(UTC)])
        assert scanned.wait(5)
        assert reported.wait(5)
    finally:
        executor.shutdown(wait=True)

    assert scan_threads
    assert telemetry_threads
    assert all(name.startswith("scheduler-control") for name in scan_threads)
    assert all(name.startswith("scheduler-control") for name in telemetry_threads)


def test_non_durable_fallback_runs_even_with_unrelated_durable_backlog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compatibility callback is not suppressed by unrelated durable work."""
    runners = object()
    durable_job = _job("task-durable", runners)
    fallback_started = threading.Event()

    def fallback_callback(*_args: object, **_kwargs: object) -> None:
        fallback_started.set()

    fallback_job = _job("task-fallback", runners, callback=fallback_callback)
    scheduler = _Scheduler([durable_job, fallback_job])
    durable_run = SimpleNamespace(
        task_id=durable_job.id,
        fire_time="2026-09-17T12:00:00Z",
    )

    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: {durable_job.id, fallback_job.id},
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_recoverable_runs",
        lambda eligible_task_ids, **_kwargs: (
            [durable_run] if durable_job.id in eligible_task_ids else []
        ),
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_execute_recoverable_run",
        lambda _run, _runners: [],
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=1,
            oldest_pending_age_seconds=0.0,
        ),
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=2,
        on_submit=lambda *_args: None,
        durable_on_submit=False,
    )
    executor.start(scheduler, "default")
    try:
        executor.submit_job(fallback_job, [datetime.now(UTC)])
        assert fallback_started.wait(5)
    finally:
        executor.shutdown(wait=True)

    assert fallback_started.is_set()


def test_non_durable_overflow_is_rejected_instead_of_dropped() -> None:
    """Compatibility submissions fail admission when no bounded slot exists."""
    runners = object()
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def first_callback(*_args: object, **_kwargs: object) -> None:
        first_started.set()
        assert release_first.wait(10)

    def second_callback(*_args: object, **_kwargs: object) -> None:
        second_started.set()

    first = _job("task-first", runners, callback=first_callback)
    second = _job("task-second", runners, callback=second_callback)
    scheduler = _Scheduler([first, second])
    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=lambda *_args: None,
        durable_on_submit=False,
    )
    executor.start(scheduler, "default")
    try:
        executor.submit_job(first, [datetime.now(UTC)])
        assert first_started.wait(5)
        with pytest.raises(MaxInstancesReachedError):
            executor.submit_job(second, [datetime.now(UTC) + timedelta(seconds=1)])
        assert not second_started.is_set()
    finally:
        release_first.set()
        executor.shutdown(wait=True)


def test_deep_same_task_prefix_does_not_hide_other_dispatchable_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows beyond the first 1,000 are found when the prefix belongs to one task."""
    runners = object()
    first = _job("task-a", runners)
    later = _job("task-b", runners)
    scheduler = _Scheduler([first, later])
    pending = [
        SimpleNamespace(
            task_id=first.id,
            fire_time=f"2026-09-17T12:{index // 60:02d}:{index % 60:02d}Z",
        )
        for index in range(1_000)
    ]
    pending.append(
        SimpleNamespace(
            task_id=later.id,
            fire_time="2026-09-18T05:00:00Z",
        )
    )
    lock = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()
    started: list[str] = []

    def recoverable_runs(
        eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        with lock:
            return [run for run in pending if run.task_id in eligible_task_ids][:limit]

    def execute_recoverable(run: SimpleNamespace, _runners: object) -> list[object]:
        with lock:
            pending.remove(run)
            started.append(run.task_id)
            if set(started) == {first.id, later.id}:
                both_started.set()
        assert release.wait(10)
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    _silence_observability(monkeypatch, pending, scheduler)

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=2,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    recovery_job = SimpleNamespace(id="scheduler-claim-recovery", max_instances=1)
    try:
        executor.submit_job(recovery_job, [datetime.now(UTC)])
        assert both_started.wait(10)
        assert set(started) == {first.id, later.id}
        assert executor.in_memory_user_callbacks == 2
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert executor.peak_in_memory_user_callbacks == 2
