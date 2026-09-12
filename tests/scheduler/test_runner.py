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
    _record_task_success_after_full_delivery,
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

    @pytest.mark.parametrize("value", ["0", "invalid"])
    def test_invalid_limit_uses_default(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, value)
        assert configured_scheduled_run_limit() == 2

    @pytest.mark.parametrize("limit", [1, 2])
    def test_real_callbacks_bound_overflow_and_keep_old_ticks(
        self, limit: int, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading
        from datetime import UTC, datetime, timedelta

        from apscheduler.events import EVENT_JOB_SUBMITTED
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.date import DateTrigger

        from infrastructure.scheduling.scheduler import executor, runner
        from infrastructure.scheduling.scheduler.storage import database, get_runs
        from infrastructure.scheduling.scheduler.types import TaskStatus

        db_path = tmp_path / "runs.db"
        monkeypatch.setattr(database, "default_run_database_path", lambda: db_path)
        monkeypatch.setenv(OPENSRE_SCHEDULER_MAX_CONCURRENT_RUNS_ENV, str(limit))
        tasks = {
            f"task-{i}": ScheduledTask(
                id=f"task-{i}",
                kind=TaskKind.MANUAL_LOOP,
                cron="* * * * *",
                provider=Provider.TELEGRAM,
            )
            for i in range(limit + 1)
        }
        # Admitted callbacks must survive queue delays longer than the former grace window.
        run_at = datetime.now(UTC) - timedelta(minutes=5)
        monkeypatch.setattr(runner, "list_tasks", lambda: list(tasks.values()))
        monkeypatch.setattr(runner, "get_task", tasks.get)
        monkeypatch.setattr(runner, "update_task", lambda _task: None)
        monkeypatch.setattr(runner, "record_task_success", lambda _task_id: None)
        monkeypatch.setattr(runner, "_make_trigger", lambda _task: DateTrigger(run_date=run_at))
        first_wave = threading.Barrier(limit + 1)
        release = threading.Event()
        all_submitted = threading.Event()
        overflow_started = threading.Event()
        submitted: set[str] = set()

        def on_submitted(event) -> None:
            submitted.add(event.job_id)
            if submitted == set(tasks):
                all_submitted.set()

        def build(task, _runners) -> str:
            if task.id == f"task-{limit}":
                overflow_started.set()
            else:
                first_wave.wait(timeout=10)
                assert release.wait(timeout=10)
            return ""

        monkeypatch.setattr(executor, "build_message", build)
        scheduler = _build_scheduler(BackgroundScheduler)
        scheduler.add_listener(on_submitted, EVENT_JOB_SUBMITTED)
        assert _register_jobs(scheduler, real_runners()) == limit + 1
        scheduler.start()
        try:
            first_wave.wait(timeout=10)
            assert all_submitted.wait(timeout=10)
            assert not overflow_started.is_set()
            assert get_runs(f"task-{limit}")[0].status is TaskStatus.PENDING
            assert all(get_runs(f"task-{i}")[0].status is TaskStatus.RUNNING for i in range(limit))
            release.set()
            assert overflow_started.wait(timeout=10)
        finally:
            release.set()
            scheduler.shutdown(wait=True)
        assert all(get_runs(task_id)[0].status is TaskStatus.SUCCESS for task_id in tasks)
        assert all(
            get_runs(task_id)[0].fire_time == _compute_fire_time(run_at) for task_id in tasks
        )

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

        def blocking_job(*_args: object) -> None:
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

        task = ScheduledTask(
            id="repeating-task",
            kind=TaskKind.MANUAL_LOOP,
            cron="* * * * *",
            provider=Provider.TELEGRAM,
        )
        monkeypatch.setattr("infrastructure.scheduling.scheduler.runner.get_task", lambda _id: task)
        monkeypatch.setattr("infrastructure.scheduling.scheduler.runner.execute_task", blocking_job)
        scheduler.add_listener(lambda _event: overlap_skipped.set(), EVENT_JOB_MAX_INSTANCES)
        scheduler.add_job(
            _scheduled_job,
            "interval",
            args=[task.id, real_runners()],
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

    def test_scheduled_job_uses_callback_fire_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import UTC, datetime

        task = ScheduledTask(
            id="task-1",
            kind=TaskKind.MANUAL_LOOP,
            cron="0 9 * * *",
            provider=Provider.SLACK,
        )
        observed: list[str] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task",
            lambda _task_id: task,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.execute_task",
            lambda _task, fire_time, _runners: observed.append(fire_time) or False,
        )

        _scheduled_job(
            task.id,
            real_runners(),
            scheduled_run_time=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        )

        assert observed == ["2026-01-15T09:00Z"]

    def test_scheduled_job_rejects_missing_fire_time(self) -> None:
        with pytest.raises(RuntimeError, match="scheduled_run_time"):
            _scheduled_job("task-1", real_runners())


class TestTaskCompletion:
    @pytest.mark.parametrize(
        ("status", "targets", "expected"),
        [
            (TaskStatus.SUCCESS, (), True),
            (
                TaskStatus.SUCCESS,
                (DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True),),
                True,
            ),
            (
                TaskStatus.SUCCESS,
                (
                    DeliveryOutcome(provider=Provider.SLACK, chat_id="C1", ok=True),
                    DeliveryOutcome(
                        provider=Provider.TELEGRAM,
                        chat_id="-100",
                        ok=False,
                        error="unavailable",
                    ),
                ),
                False,
            ),
            (TaskStatus.FAILED, (), False),
        ],
    )
    def test_finalizes_only_after_full_delivery(
        self,
        status: TaskStatus,
        targets: tuple[DeliveryOutcome, ...],
        expected: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run = TaskRun(
            task_id="task-1",
            fire_time="2026-01-01T09:00Z",
            status=status,
            targets=targets,
        )
        completed: list[str] = []
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_latest_run_for_fire_time",
            lambda _task_id, _fire_time: run,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.record_task_success",
            completed.append,
        )

        _record_task_success_after_full_delivery(run.task_id, run.fire_time)

        assert completed == ([run.task_id] if expected else [])


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

    def test_work_item_reminder_uses_exact_year(self) -> None:
        from datetime import UTC, datetime

        task = ScheduledTask(
            kind=TaskKind.WORK_ITEM_REMINDER,
            cron="",
            timezone="UTC",
            provider=Provider.SLACK,
            params={
                "work_item_id": "item-1",
                "run_at": "2027-09-12T09:00:00+00:00",
            },
        )

        result = compute_next_run(task, datetime(2026, 1, 1, tzinfo=UTC))

        assert result == "2027-09-12T09:00:00+00:00"


class TestRegisterJobs:
    def test_real_scheduler_registers_and_passes_fire_time(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from datetime import UTC, datetime, timedelta
        from threading import Event

        from apscheduler.events import EVENT_JOB_SUBMITTED
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.date import DateTrigger

        from infrastructure.scheduling.scheduler.apscheduler_executor import (
            ScheduledThreadPoolExecutor,
        )

        task = ScheduledTask(
            id="real-scheduler-task",
            kind=TaskKind.MANUAL_LOOP,
            cron="* * * * *",
            provider=Provider.TELEGRAM,
        )
        scheduled_run_time = datetime.now(UTC) + timedelta(seconds=2)
        observed_fire_times: list[str] = []
        execution_finished = Event()
        submission_finished = Event()

        def _make_date_trigger(_task: ScheduledTask) -> DateTrigger:
            return DateTrigger(run_date=scheduled_run_time)

        def _execute_task(
            _task: ScheduledTask,
            fire_time: str,
            _runners: object,
        ) -> bool:
            observed_fire_times.append(fire_time)
            execution_finished.set()
            return False

        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.list_tasks",
            lambda: [task],
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.get_task",
            lambda _task_id: task,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner._make_trigger",
            _make_date_trigger,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.update_task",
            lambda _task: None,
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.runner.execute_task",
            _execute_task,
        )

        scheduler = BackgroundScheduler(
            executors={"default": ScheduledThreadPoolExecutor(max_workers=1)}
        )
        scheduler.add_listener(lambda _event: submission_finished.set(), EVENT_JOB_SUBMITTED)
        started = False
        try:
            assert _register_jobs(scheduler, real_runners()) == 1
            scheduler.start()
            started = True
            assert execution_finished.wait(10)
            # Submission is dispatched after the scheduler removes the one-shot date job.
            assert submission_finished.wait(10)
        finally:
            if started:
                scheduler.shutdown(wait=True)

        expected_fire_time = scheduled_run_time.strftime("%Y-%m-%dT%H:%MZ")
        assert observed_fire_times == [expected_fire_time]

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

            def add_job(self, *args: object, **kwargs: object) -> None:
                _ = args
                self.job_ids.append(str(kwargs["id"]))

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

    @pytest.mark.parametrize(
        ("targets", "records_completion"),
        [
            ((DeliveryOutcome(provider=Provider.TELEGRAM, ok=True),), True),
            (
                (
                    DeliveryOutcome(provider=Provider.TELEGRAM, ok=True),
                    DeliveryOutcome(provider=Provider.SLACK, ok=False),
                ),
                False,
            ),
        ],
    )
    def test_runs_existing_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
        targets: tuple[DeliveryOutcome, ...],
        records_completion: bool,
    ) -> None:
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

        with (
            patch("infrastructure.scheduling.scheduler.runner.execute_task") as mock_exec,
            patch(
                "infrastructure.scheduling.scheduler.runner.get_latest_run_for_fire_time"
            ) as get_run,
            patch(
                "infrastructure.scheduling.scheduler.runner.record_task_success"
            ) as record_success,
        ):
            mock_exec.return_value = True
            get_run.side_effect = lambda _task_id, fire_time: TaskRun(
                task_id=task.id,
                fire_time=fire_time,
                status=TaskStatus.SUCCESS,
                targets=targets,
            )
            result = run_task_now("run_now_test", real_runners())

        assert result is True
        mock_exec.assert_called_once()
        if records_completion:
            record_success.assert_called_once_with(task.id)
        else:
            record_success.assert_not_called()
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


def test_real_scheduler_recovers_pending_after_restart(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from datetime import UTC, datetime, timedelta

    from apscheduler.triggers.date import DateTrigger

    from infrastructure.scheduling.scheduler import executor, runner
    from infrastructure.scheduling.scheduler.storage import database, get_runs, try_queue_run
    from infrastructure.scheduling.scheduler.types import TaskStatus

    db_path = tmp_path / "runs.db"
    monkeypatch.setattr(database, "default_run_database_path", lambda: db_path)
    task = ScheduledTask(
        id="restart-task",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * *",
        provider=Provider.TELEGRAM,
    )
    fire_time = "2026-01-01T09:00Z"
    assert try_queue_run(task.id, fire_time)
    monkeypatch.setattr(runner, "list_tasks", lambda: [task])
    monkeypatch.setattr(runner, "get_task", lambda _id: task)
    monkeypatch.setattr(runner, "update_task", lambda _task: None)
    monkeypatch.setattr(runner, "record_task_success", lambda _task_id: None)
    future = datetime.now(UTC) + timedelta(days=1)
    monkeypatch.setattr(runner, "_make_trigger", lambda _task: DateTrigger(run_date=future))
    built = threading.Event()

    def build(_task, _runners) -> str:
        built.set()
        return ""

    monkeypatch.setattr(executor, "build_message", build)
    scheduler, count = runner.start_background_scheduler(real_runners())
    try:
        assert count == 1
        assert built.wait(timeout=10)
    finally:
        scheduler.shutdown(wait=True)
    run = get_runs(task.id)[0]
    assert run.fire_time == fire_time
    assert run.attempt == 1
    assert run.status is TaskStatus.SUCCESS


@pytest.mark.parametrize("missing", [False, True])
def test_skipped_callback_cannot_finalize_another_owner(
    missing: bool, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from infrastructure.scheduling.scheduler import runner
    from infrastructure.scheduling.scheduler.storage import database, get_runs, try_claim

    db_path = tmp_path / "runs.db"
    monkeypatch.setattr(database, "default_run_database_path", lambda: db_path)
    task = ScheduledTask(
        id="owned-task",
        kind=TaskKind.MANUAL_LOOP,
        cron="* * * * *",
        provider=Provider.TELEGRAM,
        enabled=False,
    )
    run_at = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert try_claim(task.id, _compute_fire_time(run_at)) is not None
    monkeypatch.setattr(runner, "get_task", lambda _id: None if missing else task)
    _scheduled_job(task.id, real_runners(), scheduled_run_time=run_at)
    assert get_runs(task.id)[0].status is TaskStatus.RUNNING
