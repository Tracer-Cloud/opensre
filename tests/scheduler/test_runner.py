"""Tests for the scheduler runner."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from config.constants.turn_concurrency import OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
from infrastructure.scheduling.scheduler.runner import (
    _build_scheduler,
    _compute_fire_time,
    _make_trigger,
    _queue_scheduled_run,
    _register_jobs,
    _scheduled_job,
    compute_next_run,
    configured_scheduled_run_limit,
    refresh_background_scheduler,
    resync_scheduler_jobs,
    run_task_now,
)
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    ScheduledTask,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from tests.scheduler._bundle import real_runners


class TestMakeTrigger:
    def test_valid_cron(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * 1-5",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        trigger = _make_trigger(task)
        assert trigger is not None

    def test_invalid_cron_too_few_fields(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 *",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError, match="5 fields"):
            _make_trigger(task)

    def test_invalid_cron_bad_values(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="61 25 * * *",
            timezone="UTC",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError):
            _make_trigger(task)

    def test_invalid_timezone(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            timezone="Invalid/Timezone",
            provider=Provider.TELEGRAM,
        )
        with pytest.raises(ValueError):
            _make_trigger(task)

    def test_valid_timezone(self) -> None:
        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * 1-5",
            timezone="Europe/London",
            provider=Provider.TELEGRAM,
        )
        trigger = _make_trigger(task)
        assert trigger is not None


class TestScheduledAdmission:
    def test_queues_exact_fire_time_before_worker_submission(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import UTC, datetime

        claims: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.try_queue_run",
            lambda task_id, fire_time: claims.append((task_id, fire_time)),
        )
        _queue_scheduled_run(
            "task-1",
            datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        )
        assert claims == [("task-1", "2026-01-15T09:00Z")]


class TestScheduledConcurrency:
    def test_configured_limit_defaults_to_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, raising=False)
        assert configured_scheduled_run_limit() == 2

    @pytest.mark.parametrize("limit", [1, 2])
    def test_distinct_jobs_overlap_up_to_limit(
        self, limit: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading
        from datetime import UTC, datetime, timedelta

        from apscheduler.events import EVENT_JOB_SUBMITTED
        from apscheduler.schedulers.background import BackgroundScheduler

        monkeypatch.setenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, str(limit))
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.try_queue_run",
            lambda _task_id, _fire_time: True,
        )
        scheduler = _build_scheduler(BackgroundScheduler)
        entered = threading.Barrier(limit + 1)
        release = threading.Event()
        submitted = threading.Event()

        def on_submitted(_event: object) -> None:
            submitted.set()

        def blocking_job(scheduled_run_time: datetime | None = None) -> None:
            _ = scheduled_run_time
            entered.wait(timeout=5)
            release.wait(timeout=5)

        run_at = datetime.now(UTC) + timedelta(milliseconds=200)
        scheduler.add_listener(on_submitted, EVENT_JOB_SUBMITTED)
        for index in range(limit):
            scheduler.add_job(blocking_job, "date", run_date=run_at, id=f"job-{index}")
        scheduler.start()
        try:
            entered.wait(timeout=5)
        finally:
            release.set()
            assert submitted.wait(timeout=5)
            scheduler.shutdown(wait=True)

    def test_overflow_waits_for_a_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading
        from datetime import UTC, datetime, timedelta

        from apscheduler.events import EVENT_JOB_SUBMITTED
        from apscheduler.schedulers.background import BackgroundScheduler

        monkeypatch.setenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, "2")
        scheduler = _build_scheduler(BackgroundScheduler)
        first_wave = threading.Barrier(3)
        release = threading.Event()
        overflow_started = threading.Event()
        all_submitted = threading.Event()
        submitted_count = 0
        queued_claims: list[tuple[str, str]] = []

        def record_queued_run(task_id: str, fire_time: str) -> bool:
            queued_claims.append((task_id, fire_time))
            return True

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.try_queue_run",
            record_queued_run,
        )

        def on_submitted(_event: object) -> None:
            nonlocal submitted_count
            submitted_count += 1
            if submitted_count == 3:
                all_submitted.set()

        def blocking_job(scheduled_run_time: datetime | None = None) -> None:
            _ = scheduled_run_time
            first_wave.wait(timeout=5)
            release.wait(timeout=5)

        def overflow_job(scheduled_run_time: datetime | None = None) -> None:
            _ = scheduled_run_time
            overflow_started.set()

        run_at = datetime.now(UTC) + timedelta(milliseconds=200)
        scheduler.add_listener(on_submitted, EVENT_JOB_SUBMITTED)
        scheduler.add_job(blocking_job, "date", run_date=run_at, id="first")
        scheduler.add_job(blocking_job, "date", run_date=run_at, id="second")
        scheduler.add_job(overflow_job, "date", run_date=run_at, id="z-overflow")
        scheduler.start()
        try:
            first_wave.wait(timeout=5)
            assert not overflow_started.is_set()
            assert all_submitted.wait(timeout=5)
            assert {task_id for task_id, _ in queued_claims} == {
                "first",
                "second",
                "z-overflow",
            }
            release.set()
            assert overflow_started.wait(timeout=5)
        finally:
            release.set()
            scheduler.shutdown(wait=True)

    def test_same_job_never_overlaps_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading
        from datetime import UTC, datetime, timedelta

        from apscheduler.events import EVENT_JOB_MAX_INSTANCES
        from apscheduler.schedulers.background import BackgroundScheduler

        monkeypatch.setenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, "2")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.try_queue_run",
            lambda _task_id, _fire_time: True,
        )
        scheduler = _build_scheduler(BackgroundScheduler)
        entered = threading.Barrier(2)
        release = threading.Event()
        overlap_skipped = threading.Event()
        active = 0
        peak_active = 0
        active_lock = threading.Lock()

        def blocking_job(scheduled_run_time: datetime | None = None) -> None:
            _ = scheduled_run_time
            nonlocal active, peak_active
            with active_lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                entered.wait(timeout=5)
                release.wait(timeout=5)
            finally:
                with active_lock:
                    active -= 1

        scheduler.add_listener(lambda _event: overlap_skipped.set(), EVENT_JOB_MAX_INSTANCES)
        scheduler.add_job(
            blocking_job,
            "interval",
            seconds=0.05,
            next_run_time=datetime.now(UTC) + timedelta(milliseconds=100),
            id="repeating-task",
        )
        scheduler.start()
        try:
            entered.wait(timeout=5)
            assert overlap_skipped.wait(timeout=5)
            scheduler.pause()
            assert peak_active == 1
        finally:
            release.set()
            scheduler.shutdown(wait=True)


class TestScheduledJobOwnership:
    @pytest.mark.parametrize(
        "task",
        [
            None,
            ScheduledTask(
                id="task-1",
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.TELEGRAM,
                enabled=False,
            ),
        ],
    )
    def test_duplicate_skipped_callback_cannot_complete_another_hosts_run(
        self,
        task: ScheduledTask | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        completed: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task",
            lambda _task_id: task,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.try_start_run",
            lambda _task_id, _fire_time: None,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.complete_run",
            lambda _claim, **_kwargs: completed.append(("claimed", "claimed")),
        )

        from datetime import UTC, datetime

        _scheduled_job(
            "task-1",
            real_runners(),
            scheduled_run_time=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        )

        assert completed == []


class TestComputeFireTime:
    def test_with_utc_datetime(self) -> None:
        from datetime import UTC, datetime

        dt = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
        result = _compute_fire_time(dt)
        assert result == "2026-01-15T09:00Z"

    def test_with_non_utc_datetime(self) -> None:
        from datetime import datetime, timedelta, timezone

        # UTC+5:30
        tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2026, 1, 15, 14, 30, tzinfo=tz)
        result = _compute_fire_time(dt)
        # 14:30 IST = 09:00 UTC
        assert result == "2026-01-15T09:00Z"


class TestComputeNextRun:
    def test_returns_next_utc_fire_time(self) -> None:
        from datetime import UTC, datetime

        task = ScheduledTask(
            kind=TaskKind.MANUAL_LOOP,
            cron="0 8 * * 1-5",
            timezone="UTC",
            provider=Provider.SLACK,
        )

        result = compute_next_run(task, datetime(2026, 8, 5, 7, 30, tzinfo=UTC))

        assert result == "2026-08-05T08:00:00+00:00"


class TestRegisterJobs:
    def test_applies_task_filter(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
        from infrastructure.scheduling.scheduler.storage.task_store import add_task

        class _FakeScheduler:
            def __init__(self) -> None:
                self.job_ids: list[str] = []
                self.job_options: list[dict[str, object]] = []

            def add_listener(self, *_args: object) -> None:
                return None

            def add_job(self, *args: object, **kwargs: object) -> None:
                _ = args
                self.job_ids.append(str(kwargs["id"]))
                self.job_options.append(kwargs)

        store_path = tmp_path / "tasks.json"
        monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store_path)
        add_task(
            ScheduledTask(
                id="prompt-loop",
                kind=TaskKind.MANUAL_LOOP,
                cron="* * * * *",
                provider=Provider.INTERACTIVE_SHELL,
                params={LOOP_PROMPT_PARAM: "Report stars"},
            ),
            store_path,
        )
        add_task(
            ScheduledTask(
                id="digest",
                kind=TaskKind.SENTRY_MORNING_DIGEST,
                cron="0 9 * * *",
                provider=Provider.TELEGRAM,
            ),
            store_path,
        )

        scheduler = _FakeScheduler()
        count = _register_jobs(
            scheduler,
            real_runners(),
            task_filter=lambda task: bool(task.params.get(LOOP_PROMPT_PARAM)),
        )

        assert count == 1
        assert scheduler.job_ids == ["prompt-loop"]
        assert scheduler.job_options[0]["max_instances"] == 1

    def test_resync_removes_stale_jobs(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
        from infrastructure.scheduling.scheduler.storage.task_store import add_task

        class _FakeJob:
            def __init__(self, job_id: str) -> None:
                self.id = job_id

        class _FakeScheduler:
            def __init__(self) -> None:
                self.jobs: dict[str, _FakeJob] = {"stale": _FakeJob("stale")}

            def add_listener(self, *_args: object) -> None:
                return None

            def add_job(self, *args: object, **kwargs: object) -> None:
                _ = args
                job_id = str(kwargs["id"])
                self.jobs[job_id] = _FakeJob(job_id)

            def get_jobs(self) -> list[_FakeJob]:
                return list(self.jobs.values())

            def remove_job(self, job_id: str) -> None:
                self.jobs.pop(job_id, None)

        store_path = tmp_path / "tasks.json"
        monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store_path)
        add_task(
            ScheduledTask(
                id="keep",
                kind=TaskKind.MANUAL_LOOP,
                cron="0 9 * * *",
                provider=Provider.TELEGRAM,
            ),
            store_path,
        )

        scheduler = _FakeScheduler()
        count = resync_scheduler_jobs(scheduler, real_runners())

        assert count == 1
        assert set(scheduler.jobs) == {"keep", "scheduler-claim-recovery"}

    def test_refresh_starts_when_scheduler_was_idle(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sentinel = object()

        def _start_background_scheduler(_runners, *, task_filter=None):
            _ = task_filter
            return sentinel, 2

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.start_background_scheduler",
            _start_background_scheduler,
        )
        scheduler, count = refresh_background_scheduler(None, real_runners())
        assert scheduler is sentinel
        assert count == 2


class TestRunTaskNow:
    def test_nonexistent_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task",
            lambda _task_id: None,
        )
        assert run_task_now("nonexistent", real_runners()) is False

    def test_runs_existing_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = ScheduledTask(
            id="run_now_test",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task", lambda _task_id: task
        )

        with patch("infrastructure.scheduling.scheduler.runner.execute_task") as mock_exec:
            mock_exec.return_value = True
            result = run_task_now("run_now_test", real_runners())

        assert result is True
        mock_exec.assert_called_once()
        # Verify fire_time has seconds (ad-hoc format) and ends with Z
        call_args = mock_exec.call_args
        fire_time = call_args[0][1]
        assert fire_time.endswith("Z")
        assert "T" in fire_time
        # Ad-hoc runs use second-precision to avoid colliding with scheduled runs
        assert len(fire_time.split("T")[1].rstrip("Z").split(":")) == 3

    def test_only_failed_with_no_prior_run_refuses_rather_than_widening(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown history must never become "deliver to everyone"."""
        task = ScheduledTask(
            id="run_now_no_history",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task", lambda _task_id: task
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.get_latest_targeted_run",
            lambda _task_id: None,
        )

        with patch("infrastructure.scheduling.scheduler.runner.execute_task") as mock_exec:
            mock_exec.return_value = True
            result = run_task_now("run_now_no_history", real_runners(), only_failed=True)

        assert result is False
        mock_exec.assert_not_called()

    def test_only_failed_narrows_to_the_failed_destinations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = ScheduledTask(
            id="run_now_partial",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.INTERACTIVE_SHELL,
        )
        last_run = TaskRun(
            task_id="run_now_partial",
            fire_time="2026-01-01T09:00",
            status=TaskStatus.SUCCESS,
            targets=(
                DeliveryOutcome(provider=Provider.INTERACTIVE_SHELL, ok=True, message_id="local:1"),
                DeliveryOutcome(
                    provider=Provider.SLACK, chat_id="C1", ok=False, error="webhook missing"
                ),
            ),
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task", lambda _task_id: task
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.get_latest_targeted_run",
            lambda _task_id: last_run,
        )

        with patch("infrastructure.scheduling.scheduler.runner.execute_task") as mock_exec:
            mock_exec.return_value = True
            run_task_now("run_now_partial", real_runners(), only_failed=True)

        assert mock_exec.call_args.kwargs["target_filter"] == frozenset({(Provider.SLACK, "C1")})

    def test_only_failed_with_a_fully_successful_prior_run_retries_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = ScheduledTask(
            id="run_now_all_ok",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.TELEGRAM,
            chat_id="-100",
        )
        last_run = TaskRun(
            task_id="run_now_all_ok",
            fire_time="2026-01-01T09:00",
            status=TaskStatus.SUCCESS,
            targets=(DeliveryOutcome(provider=Provider.TELEGRAM, chat_id="-100", ok=True),),
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task", lambda _task_id: task
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.storage.get_latest_targeted_run",
            lambda _task_id: last_run,
        )

        with patch("infrastructure.scheduling.scheduler.runner.execute_task") as mock_exec:
            mock_exec.return_value = True
            run_task_now("run_now_all_ok", real_runners(), only_failed=True)

        assert mock_exec.call_args.kwargs["target_filter"] == frozenset()


class TestStartSchedulerIdle:
    """start_scheduler exits on empty for the CLI, idles for a dedicated service."""

    def test_empty_exits_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from infrastructure.scheduling.scheduler import runner

        monkeypatch.setattr(runner, "_register_jobs", lambda _scheduler, _runners, **_kw: 0)
        monkeypatch.setattr(runner, "record_scheduler_service_operation", lambda *_a, **_k: None)
        with pytest.raises(SystemExit):
            runner.start_scheduler(real_runners())

    def test_empty_idles_when_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import apscheduler.schedulers.blocking as blocking

        from infrastructure.scheduling.scheduler import runner

        started: list[bool] = []

        class _FakeScheduler:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def start(self) -> None:
                started.append(True)  # no-op instead of blocking forever

            def shutdown(self, wait: bool = False) -> None:
                pass

        monkeypatch.setattr(blocking, "BlockingScheduler", _FakeScheduler)
        monkeypatch.setattr(runner, "_register_jobs", lambda _scheduler, _runners, **_kw: 0)
        monkeypatch.setattr(runner, "record_scheduler_service_operation", lambda *_a, **_k: None)
        monkeypatch.setattr(runner.signal, "signal", lambda *_a, **_k: None)

        # Must not raise the "no tasks" SystemExit; reaches the (mocked) start.
        runner.start_scheduler(real_runners(), idle_when_empty=True)
        assert started == [True]
