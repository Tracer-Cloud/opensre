"""Tests for the gateway's shutdown record: a clean stop is told apart from an interrupted one."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    GATEWAY_STOP_TIMEOUT_SECONDS_ENV,
    MAX_STOP_TIMEOUT_SECONDS,
)
from gateway.core.lifecycle.controller import GatewayController
from gateway.core.process.shutdown_budget import stop_timeout_from_environment
from gateway.core.process.shutdown_record import (
    CLEAN,
    FIRST_START,
    UNCLEAN_CHAT_WORKERS,
    UNCLEAN_KILLED,
    UNCLEAN_SCHEDULED_JOBS,
    UNCLEAN_UNREADABLE,
    describe_previous_shutdown,
    record_running,
    shutdown_record_path,
)


class _SchedulerWithARunningJob:
    """A scheduler whose ``shutdown(wait=True)`` blocks until its job is released."""

    def __init__(self) -> None:
        self.job_released = threading.Event()
        self.shutdown_waits: list[bool] = []

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_waits.append(wait)
        if wait:
            self.job_released.wait()


def test_a_process_that_was_killed_is_reported_as_unclean_on_the_next_start() -> None:
    # Arrange: nothing has run yet
    before_any_start = describe_previous_shutdown()

    # Act: a process starts and dies without reaching stop()
    record_running()
    after_a_kill = describe_previous_shutdown()

    # Assert
    assert before_any_start == FIRST_START
    assert after_a_kill == UNCLEAN_KILLED


def test_a_stop_where_everything_finished_is_recorded_as_clean() -> None:
    # Arrange
    controller = GatewayController()
    record_running()
    surfaces = MagicMock()
    surfaces.stop.return_value = True
    controller.surfaces = surfaces
    finished_scheduler = _SchedulerWithARunningJob()
    finished_scheduler.job_released.set()
    controller.scheduler = finished_scheduler

    # Act
    stopped = controller.stop(timeout=4.0)

    # Assert: the scheduler was asked to wait for its jobs, not to drop them.
    assert stopped is True
    assert finished_scheduler.shutdown_waits == [True]
    assert describe_previous_shutdown() == CLEAN


def test_a_scheduled_job_that_outlasts_the_budget_makes_the_stop_unclean_but_not_endless() -> None:
    # Arrange
    controller = GatewayController()
    stuck_scheduler = _SchedulerWithARunningJob()
    controller.scheduler = stuck_scheduler
    surfaces = MagicMock()
    surfaces.stop.return_value = True
    controller.surfaces = surfaces

    # Act
    try:
        stopped = controller.stop(timeout=0.4)
    finally:
        stuck_scheduler.job_released.set()

    # Assert: stop returned, chat workers still got their turn, and the record says what was cut.
    assert stopped is True
    surfaces.stop.assert_called_once()
    assert describe_previous_shutdown() == UNCLEAN_SCHEDULED_JOBS


def test_a_second_signal_during_a_slow_stop_keeps_the_first_result() -> None:
    # Arrange: the first stop timed out on a scheduled job
    controller = GatewayController()
    stuck_scheduler = _SchedulerWithARunningJob()
    controller.scheduler = stuck_scheduler
    surfaces = MagicMock()
    surfaces.stop.return_value = True
    controller.surfaces = surfaces
    try:
        first = controller.stop(timeout=0.4)
    finally:
        stuck_scheduler.job_released.set()

    # Act: another SIGTERM arrives and calls stop again
    second = controller.stop(timeout=0.4)

    # Assert: same answer, nothing stopped twice, and the record still says what was cut.
    assert first is True and second is True
    surfaces.stop.assert_called_once()
    assert describe_previous_shutdown() == UNCLEAN_SCHEDULED_JOBS


def test_chat_workers_that_did_not_stop_in_time_make_the_stop_unclean() -> None:
    # Arrange
    controller = GatewayController()
    surfaces = MagicMock()
    surfaces.stop.return_value = False
    controller.surfaces = surfaces

    # Act
    stopped = controller.stop(timeout=1.0)

    # Assert
    assert stopped is False
    assert describe_previous_shutdown() == UNCLEAN_CHAT_WORKERS


def test_a_torn_record_reads_as_unclean_instead_of_clean() -> None:
    # Arrange
    path = shutdown_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"state": "stop', encoding="utf-8")

    # Act
    described = describe_previous_shutdown()

    # Assert
    assert described == UNCLEAN_UNREADABLE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, DEFAULT_STOP_TIMEOUT_SECONDS),
        ("110", 110.0),
        ("9000", MAX_STOP_TIMEOUT_SECONDS),
        ("0", DEFAULT_STOP_TIMEOUT_SECONDS),
        ("-5", DEFAULT_STOP_TIMEOUT_SECONDS),
        ("soon", DEFAULT_STOP_TIMEOUT_SECONDS),
        ("nan", DEFAULT_STOP_TIMEOUT_SECONDS),
        ("inf", DEFAULT_STOP_TIMEOUT_SECONDS),
    ],
)
def test_the_stop_budget_comes_from_the_environment_within_the_fargate_ceiling(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: float
) -> None:
    # Arrange
    if value is None:
        monkeypatch.delenv(GATEWAY_STOP_TIMEOUT_SECONDS_ENV, raising=False)
    else:
        monkeypatch.setenv(GATEWAY_STOP_TIMEOUT_SECONDS_ENV, value)

    # Act
    seconds = stop_timeout_from_environment()

    # Assert
    assert seconds == expected
