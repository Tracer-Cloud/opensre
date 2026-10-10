"""Cron expression parsing shared by the scheduler runner and every ``add`` command.

Two shapes are accepted: the standard five-field crontab line
(``minute hour day month day_of_week``) and a six-field line with a leading
seconds field (``second minute hour day month day_of_week``). The seconds form
exists for polling loops such as the CI repair loop, which must react within
half a minute; a five-field line can never fire more than once per minute.

Weekday numbers mean what they mean in crontab (0 and 7 are Sunday, 1 is
Monday), although APScheduler 3.x counts from 0 = Monday; every trigger is
built through :func:`build_cron_trigger`, which translates them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import groupby
from typing import Any

#: A standard crontab line: minute, hour, day, month, day_of_week.
CRON_FIELD_COUNT = 5
#: A crontab line with a leading seconds field.
CRON_FIELD_COUNT_WITH_SECONDS = 6

CRON_FORMAT_HELP = (
    "minute hour day month day_of_week, "
    "or second minute hour day month day_of_week for sub-minute polling"
)
CRON_FIELD_COUNT_ERROR = (
    f"cron expression must have {CRON_FIELD_COUNT} fields"
    f" ({CRON_FIELD_COUNT_WITH_SECONDS} with a leading seconds field)"
)

#: APScheduler 3.x day names, indexed like ``datetime.weekday()`` (0 = Monday).
_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
#: Crontab weekday numbers run 0-7; both 0 and 7 are Sunday.
_CRONTAB_LAST_WEEKDAY = 7
_CRONTAB_WEEKDAY_ITEM = re.compile(
    r"(?:(?P<every>\*)|(?P<first>\d+)(?:-(?P<last>\d+))?)(?:/(?P<step>\d+))?"
)


def day_of_week_names(field: str) -> str:
    """Spell a crontab ``day_of_week`` field in the day names APScheduler 3.x reads.

    Numeric items (``0``, ``1-5``, ``1-5/2``, ``*/2``) are read the crontab way
    and become names; a step is expanded into a list, because APScheduler
    ignores a step written after a name range. Names and a bare ``*`` mean the
    same to both and pass through. Raises ``ValueError`` for a number above 7,
    a backwards range, or a zero step.
    """
    passthrough: list[str] = []
    days: set[int] = set()
    for item in field.lower().split(","):
        match = _CRONTAB_WEEKDAY_ITEM.fullmatch(item)
        if match is None or item == "*":
            passthrough.append(item)
        else:
            days.update(_crontab_weekdays(item, match))
    return ",".join([*_weekday_ranges(days), *passthrough])


def _crontab_weekdays(item: str, match: re.Match[str]) -> set[int]:
    """APScheduler weekday indexes for one numeric crontab item."""
    step = int(match["step"] or 1)
    if match["every"]:
        first, last = 0, _CRONTAB_LAST_WEEKDAY
    else:
        first = int(match["first"])
        if match["last"] is not None:
            last = int(match["last"])
        else:
            # ``N/step`` runs to the end of the week, like ``*/step``.
            last = first if match["step"] is None else _CRONTAB_LAST_WEEKDAY
    if max(first, last) > _CRONTAB_LAST_WEEKDAY:
        raise ValueError(f"day_of_week numbers run 0-7, with 0 and 7 for Sunday: {item!r}")
    if first > last:
        raise ValueError(f"day_of_week range runs backwards: {item!r}")
    if step < 1:
        raise ValueError(f"day_of_week step must be at least 1: {item!r}")
    # Crontab 1 (Monday) is APScheduler 0; crontab 0 and 7 (Sunday) are both 6.
    return {(day - 1) % len(_WEEKDAY_NAMES) for day in range(first, last + 1, step)}


def _weekday_ranges(days: set[int]) -> list[str]:
    """Day names for APScheduler weekday indexes, consecutive days joined into a range."""
    if len(days) == len(_WEEKDAY_NAMES):
        return ["*"]
    ranges: list[str] = []
    for _, run in groupby(enumerate(sorted(days)), key=lambda pair: pair[1] - pair[0]):
        run_days = [day for _, day in run]
        first, last = _WEEKDAY_NAMES[run_days[0]], _WEEKDAY_NAMES[run_days[-1]]
        ranges.append(first if first == last else f"{first}-{last}")
    return ranges


def build_cron_trigger(cron: str, timezone: str | None) -> Any:
    """Build an APScheduler ``CronTrigger`` for a five- or six-field crontab expression.

    Raises ``ValueError`` when the field count, a field value, or the timezone
    is invalid, so callers reject a schedule before it is stored.
    """
    from apscheduler.triggers.cron import CronTrigger

    parts = cron.split()
    if len(parts) == CRON_FIELD_COUNT:
        second: str | None = None
        minute, hour, day, month, day_of_week = parts
    elif len(parts) == CRON_FIELD_COUNT_WITH_SECONDS:
        second, minute, hour, day, month, day_of_week = parts
    else:
        raise ValueError(f"{CRON_FIELD_COUNT_ERROR}: {cron!r}")

    try:
        return CronTrigger(
            second=second,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week_names(day_of_week),
            timezone=timezone,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"invalid cron expression or timezone: {exc}") from exc


_MIN_FIRE_GAP_SECONDS = 60 * 60
_FIRE_SAMPLES = 48


def _normalized_cron(cron: str) -> str:
    return " ".join(cron.split())


def _fires_more_than_hourly(cron: str, timezone: str | None) -> bool:
    """Whether two of the next fires are less than an hour apart."""
    trigger = build_cron_trigger(cron, timezone)
    previous = None
    cursor = datetime.now(UTC)
    for _ in range(_FIRE_SAMPLES):
        fire = trigger.get_next_fire_time(previous, cursor)
        if fire is None:
            return False
        if previous is not None and (fire - previous).total_seconds() < _MIN_FIRE_GAP_SECONDS:
            return True
        previous = fire
        cursor = fire
    return False


def _earliest_field_value(field: str) -> str:
    """Return the earliest value in a validated minute or hour field."""
    starts = (item.split("/", 1)[0].split("-", 1)[0] for item in field.split(","))
    return str(min(0 if start == "*" else int(start) for start in starts))


def cap_cron_at_most_hourly(cron: str, timezone: str | None = None) -> str:
    """Return ``cron``, or a coarser expression that fires at most once an hour.

    A minute list or step (``8,23,38,53``, ``*/15``) keeps its earliest minute.
    A leading seconds field is dropped. Day, month, and weekday stay, so a
    daily schedule is not turned into an hourly one. An expression that
    already waits at least an hour is unchanged, including its whitespace
    collapsed to single spaces.
    """
    normalized = _normalized_cron(cron)
    if not _fires_more_than_hourly(normalized, timezone):
        return normalized
    parts = normalized.split()
    if len(parts) == CRON_FIELD_COUNT_WITH_SECONDS:
        parts = parts[1:]
    parts[0] = _earliest_field_value(parts[0])
    capped = " ".join(parts)
    if _fires_more_than_hourly(capped, timezone):
        parts[1] = _earliest_field_value(parts[1])
        capped = " ".join(parts)
    return capped


__all__ = [
    "CRON_FIELD_COUNT",
    "CRON_FIELD_COUNT_ERROR",
    "CRON_FIELD_COUNT_WITH_SECONDS",
    "CRON_FORMAT_HELP",
    "build_cron_trigger",
    "cap_cron_at_most_hourly",
    "day_of_week_names",
]
