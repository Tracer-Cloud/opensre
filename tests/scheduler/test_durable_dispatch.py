"""Durable-dispatch invariants spanning admission, restart, and same-task exclusion."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apscheduler.events import JobEvent
from apscheduler.executors.base import MaxInstancesReachedError

from infrastructure.scheduling.scheduler import apscheduler_executor as scheduler_executor


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


def _job(task_id: str, runners: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        max_instances=1,
        misfire_grace_time=None,
        func=lambda **_kwargs: None,
        args=(task_id, runners),
        kwargs={},
        _jobstore_alias="default",
    )


def _silence_backlog_events(
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


def test_restart_recovery_drains_existing_backlog_without_new_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh executor discovers durable work from registered jobs on recovery wake."""
    runners = object()
    jobs = [_job(f"task-{index}", runners) for index in range(4)]
    scheduler = _Scheduler(jobs)
    pending = [
        SimpleNamespace(
            task_id=job.id,
            fire_time=f"2026-09-17T12:00:0{index}Z",
        )
        for index, job in enumerate(jobs)
    ]
    lock = threading.Lock()
    executed: list[str] = []
    all_done = threading.Event()

    def recoverable_runs(
        eligible_task_ids: set[str],
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        with lock:
            return [run for run in pending if run.task_id in eligible_task_ids][:limit]

    def execute_recoverable(
        run: SimpleNamespace,
        observed_runners: object,
    ) -> list[object]:
        assert observed_runners is runners
        with lock:
            pending.remove(run)
            executed.append(run.task_id)
            if len(executed) == len(jobs):
                all_done.set()
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    _silence_backlog_events(monkeypatch, pending, scheduler)

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=2,
        on_submit=lambda *_args: None,
    )
    executor.start(scheduler, "default")
    recovery_job = SimpleNamespace(id="scheduler-claim-recovery", max_instances=1)
    try:
        executor.submit_job(recovery_job, [datetime.now(UTC)])
        assert all_done.wait(10)
    finally:
        executor.shutdown(wait=True)

    assert len(executed) == len(jobs)
    assert set(executed) == {job.id for job in jobs}
    assert not pending
    assert executor.peak_in_memory_user_callbacks <= 2


def test_same_task_second_tick_is_durable_then_runs_after_first_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Max-instance overlap is observable but no admitted tick is silently lost."""
    runners = object()
    job = _job("task-1", runners)
    scheduler = _Scheduler([job])
    pending: list[SimpleNamespace] = []
    lock = threading.Lock()
    started: list[str] = []
    first_started = threading.Event()
    second_done = threading.Event()
    release_first = threading.Event()

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
            started.append(run.fire_time)
            ordinal = len(started)
        if ordinal == 1:
            first_started.set()
            assert release_first.wait(10)
        else:
            second_done.set()
        return []

    monkeypatch.setattr(scheduler_executor, "_recoverable_runs", recoverable_runs)
    monkeypatch.setattr(scheduler_executor, "_execute_recoverable_run", execute_recoverable)
    _silence_backlog_events(monkeypatch, pending, scheduler)

    executor = scheduler_executor.ScheduledThreadPoolExecutor(
        max_workers=1,
        on_submit=on_submit,
    )
    executor.start(scheduler, "default")
    first = datetime.now(UTC)
    second = first + timedelta(seconds=1)
    try:
        executor.submit_job(job, [first])
        assert first_started.wait(10)
        with pytest.raises(MaxInstancesReachedError):
            executor.submit_job(job, [second])

        assert len(started) == 1
        with lock:
            assert len(pending) == 1
            assert pending[0].fire_time == second.isoformat()

        release_first.set()
        assert second_done.wait(10)
    finally:
        release_first.set()
        executor.shutdown(wait=True)

    assert started == [first.isoformat(), second.isoformat()]
    assert not pending
    assert executor.peak_in_memory_user_callbacks == 1
