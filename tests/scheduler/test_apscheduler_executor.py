"""Tests for the scheduler's APScheduler executor adapter."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, JobEvent

from infrastructure.scheduling.scheduler.apscheduler_executor import (
    ScheduledThreadPoolExecutor,
)


class _FakeScheduler:
    def __init__(self) -> None:
        self.event_codes: list[int] = []

    def _create_lock(self) -> threading.RLock:
        return threading.RLock()

    def _dispatch_event(self, event: JobEvent) -> None:
        self.event_codes.append(event.code)


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
    executor = ScheduledThreadPoolExecutor(max_workers=1)
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, run_times)
        assert started.wait(5)
    finally:
        release.set()
        executor.shutdown(wait=True)

    assert observed == run_times[1:]
    assert scheduler.event_codes == [EVENT_JOB_MISSED, EVENT_JOB_EXECUTED, EVENT_JOB_EXECUTED]


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
    executor = ScheduledThreadPoolExecutor(max_workers=1, on_submit=on_submit)
    executor.start(scheduler, "default")
    try:
        executor.submit_job(job, [datetime.now(UTC)])
    finally:
        executor.shutdown(wait=True)

    assert order == ["submitted", "started"]
