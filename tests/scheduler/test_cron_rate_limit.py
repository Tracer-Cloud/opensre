"""Prompt loops fire at most once an hour; the CI repair poller does not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants.ci_repair import CI_REPAIR_CRON, CI_REPAIR_REPORT_BUILDER
from infrastructure.scheduling.scheduler.cron_expression import (
    build_cron_trigger,
    cap_cron_at_most_hourly,
)
from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM, LOOP_REPORT_PARAM
from infrastructure.scheduling.scheduler.loops import create_manual_loop
from infrastructure.scheduling.scheduler.runner import _register_jobs
from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store
from infrastructure.scheduling.scheduler.storage.task_store import add_task, get_task
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from tests.scheduler._bundle import real_runners


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("8,23,38,53 * * * *", "8 * * * *"),
        ("0,15,30,45 * * * *", "0 * * * *"),
        ("4,19,34,49 * * * *", "4 * * * *"),
        ("12,27,42,57 * * * *", "12 * * * *"),
        ("*/15 * * * *", "15 * * * *"),
        ("* * * * *", "0 * * * *"),
        ("*/30 * * * * *", "0 * * * *"),
        ("0,30 9 * * 1", "0 9 * * 1"),
    ],
)
def test_schedules_faster_than_hourly_collapse_to_one_minute(cron: str, expected: str) -> None:
    assert cap_cron_at_most_hourly(cron, "UTC") == expected


@pytest.mark.parametrize(
    "cron",
    ["0 * * * *", "0 8,9 * * *", "0 9 * * *", "0 8,20 * * *", "0 9 * * 1-5", "30 8 * * 1"],
)
def test_hourly_or_slower_schedules_stay(cron: str) -> None:
    assert cap_cron_at_most_hourly(f"  {cron}  ", "UTC") == cron


@pytest.mark.parametrize(
    "cron",
    [
        # A stepped range starts at its first value, not at its step.
        "10-50/5 * * * *",
        "20-40/10 * * * *",
        "45-59/5 * * * *",
        "5-55/10 * * * *",
        "1-59/7 * * * *",
    ],
)
def test_the_hourly_cap_only_ever_keeps_a_time_the_loop_already_had(cron: str) -> None:
    """Capping thins a loop's fires; it must never move one to a minute it never ran at."""
    # Arrange: every time the loop was asked for, and every time the cap would fire.
    start = datetime(2026, 11, 2, tzinfo=UTC)
    requested = _fire_times(cron, start)
    capped = cap_cron_at_most_hourly(cron, "UTC")
    capped_times = _fire_times(capped, start)

    # Act
    invented = capped_times - requested

    # Assert: the cap keeps a subset of the requested times, and does fire.
    assert capped_times
    assert invented == set(), (
        f"capping {cron!r} to {capped!r} fires at times the loop never ran at: "
        f"{sorted(invented)[:3]}"
    )


def _fire_times(cron: str, start: datetime) -> set[datetime]:
    """Every fire time ``cron`` has in the day after ``start``."""
    trigger = build_cron_trigger(cron, "UTC")
    end = start + timedelta(days=1)
    times: set[datetime] = set()
    fire = trigger.get_next_fire_time(None, start)
    while fire is not None and fire < end:
        times.add(fire)
        fire = trigger.get_next_fire_time(fire, fire)
    return times


def test_create_manual_loop_stores_at_most_one_run_per_hour(tmp_path: Path) -> None:
    store_path = tmp_path / "scheduler_tasks.json"

    created = create_manual_loop(
        name="Code scanning",
        prompt="Repair code scanning alerts",
        cron="8,23,38,53 * * * *",
        channels=["interactive_shell"],
        store_path=store_path,
    )
    daily = create_manual_loop(
        name="Morning ops",
        prompt="Summarize risk",
        cron="0 8 * * *",
        channels=["interactive_shell"],
        store_path=store_path,
    )

    assert created.task.cron == "8 * * * *"
    assert daily.task.cron == "0 8 * * *"


def test_ci_repair_poller_keeps_its_seconds_cadence(tmp_path: Path) -> None:
    created = create_manual_loop(
        name="CI repair",
        prompt="Repair failing checks",
        cron=CI_REPAIR_CRON,
        channels=["interactive_shell"],
        store_path=tmp_path / "scheduler_tasks.json",
        report=CI_REPAIR_REPORT_BUILDER,
    )

    assert created.task.cron == CI_REPAIR_CRON


def test_register_persists_the_hourly_cap_and_leaves_ci_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Scheduler:
        def __init__(self) -> None:
            self.job_ids: list[str] = []

        def add_job(self, *_args: object, **kwargs: object) -> None:
            self.job_ids.append(str(kwargs["id"]))

    store_path = tmp_path / "tasks.json"
    monkeypatch.setattr(scheduler_store, "default_task_store_path", lambda: store_path)
    add_task(
        ScheduledTask(
            id="code-scanning",
            name="OpenSRE · Code scanning",
            kind=TaskKind.MANUAL_LOOP,
            cron="8,23,38,53 * * * *",
            timezone="UTC",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_PROMPT_PARAM: "Repair code scanning"},
        ),
        store_path,
    )
    add_task(
        ScheduledTask(
            id="repair",
            kind=TaskKind.MANUAL_LOOP,
            cron=CI_REPAIR_CRON,
            timezone="UTC",
            provider=Provider.INTERACTIVE_SHELL,
            params={LOOP_REPORT_PARAM: CI_REPAIR_REPORT_BUILDER},
        ),
        store_path,
    )

    assert _register_jobs(_Scheduler(), real_runners()) == 2
    stored_loop = get_task("code-scanning")
    stored_repair = get_task("repair")
    assert stored_loop is not None and stored_loop.cron == "8 * * * *"
    assert stored_repair is not None and stored_repair.cron == CI_REPAIR_CRON
