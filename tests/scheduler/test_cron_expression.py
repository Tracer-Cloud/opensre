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
        # A step after a name range is ignored by APScheduler, so steps become lists.
        ("1-5/2", {"Mon", "Wed", "Fri"}),
        ("mon-fri", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
    ],
)
def test_a_weekday_field_fires_on_its_crontab_days(day_of_week: str, fires_on: set[str]) -> None:
    assert _fire_days(f"0 10 * * {day_of_week}") == fires_on


def test_a_weekday_number_above_7_is_rejected_before_it_is_stored() -> None:
    with pytest.raises(ValueError, match="0-7"):
        build_cron_trigger("0 10 * * 8", "UTC")


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


# ---------------------------------------------------------------------------
# OR semantics: both day-of-month and day-of-week are restricted
# ---------------------------------------------------------------------------

_REF = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)  # a Friday


def _next(cron: str) -> datetime | None:
    trigger = build_cron_trigger(cron, "UTC")
    return trigger.get_next_fire_time(None, _REF)


def test_dom_and_dow_both_restricted_fires_on_next_weekday_match() -> None:
    # "0 9 1 * 1" (1st of month + every Monday).
    # From 2026-10-23 (Friday), next Monday is 2026-10-26, earlier than Nov 1.
    result = _next("0 9 1 * 1")
    assert result is not None
    assert result == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def test_dom_and_dow_both_restricted_fires_on_dom_when_earlier() -> None:
    # "0 9 1 * 0" (1st of month + every Sunday).
    # From 2026-10-23 (Friday), next Sunday is 2026-10-25, earlier than Nov 1.
    result = _next("0 9 1 * 0")
    assert result is not None
    assert result == datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def test_star_dow_not_affected_by_or_logic() -> None:
    # When dow is *, no OR trigger; single CronTrigger.
    result = _next("0 9 1 * *")
    assert result == datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def test_star_dom_not_affected_by_or_logic() -> None:
    # When dom is *, no OR trigger; fires on weekday restriction only.
    result = _next("0 9 * * 1")
    assert result == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def test_step_dom_counts_as_unrestricted() -> None:
    # "*/5" dom is unrestricted — still one CronTrigger, no OR.
    result = _next("0 9 */5 * 1")
    assert result is not None
    # Fires on next Monday 2026-10-26 (*/5 dow is unrestricted so only dow matters? No:
    # */5 dom is unrestricted, so it fires on Monday regardless of dom).
    assert result == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
