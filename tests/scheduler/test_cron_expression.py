"""Cron lines are read the crontab way, although APScheduler 3.x counts weekdays from 0 = Monday."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.runner import compute_next_run
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_SUNDAY_NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _fire_days(cron: str) -> set[str]:
    """The weekdays ``cron`` fires on during the two weeks after a Sunday noon."""
    trigger = build_cron_trigger(cron, "UTC")
    days: set[str] = set()
    fire = trigger.get_next_fire_time(None, _SUNDAY_NOON)
    while fire is not None and fire < _SUNDAY_NOON + timedelta(days=14):
        days.add(f"{fire:%a}")
        fire = trigger.get_next_fire_time(fire, fire)
    return days


@pytest.mark.parametrize(
    ("day_of_week", "fires_on"),
    [
        ("1-5", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
        ("0", {"Sun"}),
        ("7", {"Sun"}),
        ("*/2", {"Sun", "Tue", "Thu", "Sat"}),
        # A step after a name range is ignored by APScheduler, so steps become lists,
        # whether the range is written in numbers or in names.
        ("1-5/2", {"Mon", "Wed", "Fri"}),
        ("mon-fri", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
        ("mon-fri/2", {"Mon", "Wed", "Fri"}),
    ],
)
def test_a_weekday_field_fires_on_its_crontab_days(day_of_week: str, fires_on: set[str]) -> None:
    assert _fire_days(f"0 10 * * {day_of_week}") == fires_on


def _fire_months(cron: str) -> list[str]:
    """The months ``cron`` fires in during the year after a Sunday noon."""
    trigger = build_cron_trigger(cron, "UTC")
    months: list[str] = []
    fire = trigger.get_next_fire_time(None, _SUNDAY_NOON)
    while fire is not None and fire < _SUNDAY_NOON + timedelta(days=365):
        months.append(f"{fire:%b}")
        fire = trigger.get_next_fire_time(fire, fire)
    return months


def test_a_step_after_a_month_name_range_fires_quarterly() -> None:
    # APScheduler drops the step after ``jan-dec``; the numeric form is the reference.
    assert _fire_months("0 10 1 jan-dec/3 *") == _fire_months("0 10 1 1-12/3 *")
    assert _fire_months("0 10 1 jan-dec/3 *") == ["Jan", "Apr", "Jul", "Oct"]


@pytest.mark.parametrize(
    ("cron", "reason"),
    [
        ("0 10 * * 8", "0-7"),
        ("0 10 * * mon-fri/0", "step"),
        ("0 10 * * fri-mon", "backwards"),
        ("0 10 * * mon-fry", "mon-sun"),
        # APScheduler reads ``mon-fri`` and drops the rest; the whole item must parse.
        ("0 10 * * mon-fri/-1", "unrecognized"),
        ("0 10 1 jam *", "jan-dec"),
        # A name glued to a number must not be read as ``11`` (November).
        ("0 10 1 1jan *", "unrecognized"),
    ],
)
def test_an_invalid_weekday_or_month_item_is_rejected_before_it_is_stored(
    cron: str, reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        build_cron_trigger(cron, "UTC")


def test_the_stored_next_run_skips_the_weekend() -> None:
    """``next_run`` is what the store keeps and the loop list shows; it must agree with the trigger."""
    # Arrange: a weekday loop, asked after Friday's slot has passed
    task = ScheduledTask(
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
    )
    friday_after_slot = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)

    # Act
    next_run = compute_next_run(task, friday_after_slot)

    # Assert: Monday, not Saturday
    assert next_run == "2026-10-12T08:00:00+00:00"
