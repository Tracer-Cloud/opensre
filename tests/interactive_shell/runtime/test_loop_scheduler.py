"""Shell-hosted scheduler ticks are traced under the shell session that hosts them."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import bootstrap.adapters as adapters
import surfaces.interactive_shell.runtime.loop_scheduler as loop_scheduler
from infrastructure.scheduling.scheduler.runners import SchedulerRunners


class _Scheduler:
    def shutdown(self, wait: bool = False) -> None:
        del wait


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[SchedulerRunners]]:
    """Capture the runner bundle each (re)start hands the background scheduler."""
    captured: list[SchedulerRunners] = []

    def fake_start(runners: SchedulerRunners, **_kwargs: Any) -> tuple[_Scheduler, int]:
        captured.append(runners)
        return _Scheduler(), 1

    monkeypatch.setattr(loop_scheduler, "configure_process", lambda _profile: None)
    monkeypatch.setattr(loop_scheduler, "start_background_scheduler", fake_start)
    monkeypatch.setattr(
        adapters, "scheduler_runners", lambda: SchedulerRunners(agent=lambda _p: "")
    )
    monkeypatch.setattr(loop_scheduler, "_scheduler", None)
    monkeypatch.setattr(loop_scheduler, "_task_count", 0)
    monkeypatch.setattr(loop_scheduler, "_host_session", None)
    yield captured
    loop_scheduler.shutdown_loop_scheduler()


def test_host_session_is_resolved_per_tick_even_when_started_before_the_controller(
    started: list[SchedulerRunners],
) -> None:
    # A startup demo may start the scheduler before the controller names the session.
    loop_scheduler.start_loop_scheduler()
    (runners,) = started
    assert runners.host_session_id() is None

    current = {"id": "session-1"}
    loop_scheduler.start_loop_scheduler(host_session=lambda: current["id"])
    assert len(started) == 1, "an already-running scheduler is not restarted"
    assert runners.host_session_id() == "session-1"

    current["id"] = "session-2"  # /new rotated the shell session
    assert runners.host_session_id() == "session-2"


def test_reload_keeps_the_host_session(started: list[SchedulerRunners]) -> None:
    loop_scheduler.start_loop_scheduler(host_session=lambda: "session-1")
    loop_scheduler.reload_loop_scheduler()

    assert len(started) == 2
    assert started[-1].host_session_id() == "session-1"
