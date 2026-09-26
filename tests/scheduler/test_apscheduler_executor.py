"""Tests for the scheduler's APScheduler executor adapter."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, JobEvent

from infrastructure.scheduling.scheduler import apscheduler_executor as scheduler_executor


class _FakeScheduler:
    def __init__(self) -> None:
        self.event_codes: list[int] = []

    def _create_lock(self) -> threading.RLock:
        return threading.RLock()

    def _dispatch_event(self, event: JobEvent) -> None:
        self.event_codes.append(event.code)


class _DurableFakeScheduler(_FakeScheduler):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: dict[str, SimpleNamespace] = {}

    def add_job(self, job: SimpleNamespace) -> None:
        self.jobs[job.id] = job

    def get_jobs(self) -> list[SimpleNamespace]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> SimpleNamespace | None:
        return self.jobs.get(job_id)


def _fake_job(task_id: str, runners: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        max_instances=1,
        misfire_grace_time=None,
        func=lambda **_kwargs: None,
        args=(task_id, runners),
        kwargs={},
        _jobstore_alias="default",
    )


def test_worker_receives_each_eligible_fire_time_without_submission_listener() -> None:
    started = threading.Event()
    release = threading.Event()
    observed: list[datetime] = []
    now = datetime.now(UTC)
    run_times = [now - timedelta(minutes=5), now - timedelta(seconds=1), now]

    def callback(*, scheduled_run_time: datetime) -> None:
        observed.append(scheduled_run_time)
        if len(observed) == 1:
            started.set()
            assert release.wait(5)

    scheduler = _FakeScheduler()
    job = SimpleNamespace(
        id="task-1",
        max_instances=1,
        misfire_grace_time=60,
        func=callback,
        args=(),
        kwargs={},
        _jobstore_alias="default",
    )
    executor = scheduler_executor.ScheduledThreadPoolExecutor(max_workers=1)
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, run_times)
        assert started.wait(5)
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert observed == run_times[1:]
    assert scheduler.event_codes == [
        EVENT_JOB_MISSED,
        EVENT_JOB_EXECUTED,
        EVENT_JOB_EXECUTED,
    ]


def test_submission_is_persisted_before_worker_starts() -> None:
    order: list[str] = []

    def on_submit(_job_id: str, _scheduled_run_time: datetime) -> None:
        order.append("submitted")

    def callback(*, scheduled_run_time: datetime) -> None:
        _ = scheduled_run_time
        order.append("started")

    scheduler = _FakeScheduler()
    job = SimpleNamespace(
        id="task-1",
        max_instances=1,
        misfire_grace_time=None,
        func=callback,
        args=(),
        kwargs={},
        _jobstore_alias="default",
    )
    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=on_submit,
    )
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, [datetime.now(UTC)])
    finally:
        executor.shutdown(wait=True)

    assert order == ["submitted", "started"]


def test_thousand_task_burst_keeps_only_capacity_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overflow remains durable: a 1,000-task burst never submits > C user futures."""
    capacity = 2
    task_count = 1_000
    runners = object()
    scheduler = _DurableFakeScheduler()
    pending: list[SimpleNamespace] = []
    pending_lock = threading.Lock()
    started: list[str] = []
    completed = 0
    first_wave = threading.Event()
    release = threading.Event()
    all_done = threading.Event()

    jobs = [_fake_job(f"task-{index}", runners) for index in range(task_count)]
    for job in jobs:
        scheduler.add_job(job)

    def on_submit(task_id: str, scheduled_run_time: datetime) -> None:
        run = SimpleNamespace(task_id=task_id, fire_time=scheduled_run_time.isoformat())
        with pending_lock:
            pending.append(run)

    def recoverable_runs(
        eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        with pending_lock:
            return [run for run in pending if run.task_id in eligible_task_ids][:limit]

    def execute_recoverable(run: SimpleNamespace, _runners: object) -> list[object]:
        nonlocal completed
        with pending_lock:
            pending.remove(run)
            started.append(run.task_id)
            if len(started) == capacity:
                first_wave.set()
        if run.task_id in {"task-0", "task-1"}:
            assert release.wait(10)
        with pending_lock:
            completed += 1
            if completed == task_count:
                all_done.set()
        return []

    def backlog_snapshot(_eligible_task_ids: set[str]) -> SimpleNamespace:
        with pending_lock:
            return SimpleNamespace(
                pending_count=len(pending),
                oldest_pending_age_seconds=0.0 if pending else None,
            )

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: set(scheduler.jobs),
    )
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    monkeypatch.setattr(scheduler_executor, "_backlog_snapshot", backlog_snapshot)
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=capacity,
        on_submit=on_submit,
    )
    executor.start(scheduler, "default")
    now = datetime.now(UTC)
    try:
        for index, job in enumerate(jobs):
            executor.submit_job(job, [now + timedelta(seconds=index)])

        assert first_wave.wait(10)
        assert executor.in_memory_user_callbacks == capacity
        assert executor.peak_in_memory_user_callbacks == capacity
        assert len(started) == capacity

        release.set()
        assert all_done.wait(20)
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert completed == task_count
    assert not pending
    assert executor.peak_in_memory_user_callbacks == capacity


def test_recovery_control_lane_runs_while_user_pool_is_saturated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Periodic recovery remains live even when every user worker is blocked."""
    capacity = 2
    runners = object()
    scheduler = _DurableFakeScheduler()
    pending: list[SimpleNamespace] = []
    pending_lock = threading.Lock()
    release = threading.Event()
    first_wave = threading.Event()
    recovered = threading.Event()
    started: list[str] = []

    jobs = [_fake_job(f"task-{index}", runners) for index in range(3)]
    for job in jobs:
        scheduler.add_job(job)

    def on_submit(task_id: str, scheduled_run_time: datetime) -> None:
        with pending_lock:
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
        with pending_lock:
            return [run for run in pending if run.task_id in eligible_task_ids][:limit]

    def execute_recoverable(run: SimpleNamespace, _runners: object) -> list[object]:
        with pending_lock:
            pending.remove(run)
            started.append(run.task_id)
            if len(started) == capacity:
                first_wave.set()
        if run.task_id in {"task-0", "task-1"}:
            assert release.wait(10)
        if run.task_id == "task-2":
            recovered.set()
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(
        scheduler_executor,
        "_enabled_task_ids",
        lambda: set(scheduler.jobs),
    )
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    monkeypatch.setattr(
        scheduler_executor,
        "_backlog_snapshot",
        lambda _eligible: SimpleNamespace(
            pending_count=len(pending),
            oldest_pending_age_seconds=0.0,
        ),
    )
    monkeypatch.setattr(
        scheduler_executor,
        "_record_backlog_state",
        lambda *_args, **_kwargs: None,
    )

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=capacity,
        on_submit=on_submit,
    )
    executor.start(scheduler, "default")
    now = datetime.now(UTC)
    try:
        executor.submit_job(jobs[0], [now])
        executor.submit_job(jobs[1], [now + timedelta(seconds=1)])
        assert first_wave.wait(10)
        assert executor.in_memory_user_callbacks == capacity

        # Seed work which is discoverable only through recovery, then fire the
        # periodic control job while both user workers remain blocked.
        with pending_lock:
            pending.append(
                SimpleNamespace(
                    task_id="task-2",
                    fire_time="recovery-fire-time",
                )
            )
        before = executor.control_cycles
        recovery_job = SimpleNamespace(id="scheduler-claim-recovery", max_instances=1)
        executor.submit_job(recovery_job, [now + timedelta(seconds=2)])

        deadline = threading.Event()
        for _ in range(100):
            if executor.control_cycles > before:
                break
            deadline.wait(0.01)
        assert executor.control_cycles > before
        assert not recovered.is_set()

        release.set()
        assert recovered.wait(10)
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert executor.peak_in_memory_user_callbacks == capacity


def test_shutdown_does_not_join_control_lookup_blocked_on_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown cannot wait on control work that is blocked in scheduler lookup."""
    scheduler = _DurableFakeScheduler()
    lookup_started = threading.Event()
    release_lookup = threading.Event()
    lookup_returned = threading.Event()
    control_exited = threading.Event()
    shutdown_returned = threading.Event()
    shutdown_errors: list[Exception] = []

    def blocked_get_jobs() -> list[SimpleNamespace]:
        lookup_started.set()
        assert release_lookup.wait(5)
        lookup_returned.set()
        return []

    monkeypatch.setattr(scheduler, "get_jobs", blocked_get_jobs)
    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    original_fill_capacity = executor._fill_capacity

    def tracked_fill_capacity() -> None:
        try:
            original_fill_capacity()
        finally:
            control_exited.set()

    monkeypatch.setattr(executor, "_fill_capacity", tracked_fill_capacity)
    executor._request_drain()
    assert lookup_started.wait(5)

    def shutdown_executor() -> None:
        try:
            executor.shutdown(wait=True)
        except Exception as exc:  # pragma: no cover - surfaced below
            shutdown_errors.append(exc)
        finally:
            shutdown_returned.set()

    shutdown_thread = threading.Thread(target=shutdown_executor)
    shutdown_thread.start()
    try:
        assert shutdown_returned.wait(2)
        assert not lookup_returned.is_set()
    finally:
        release_lookup.set()
        shutdown_thread.join(timeout=5)

    assert not shutdown_thread.is_alive()
    assert not shutdown_errors
    assert control_exited.wait(5)
