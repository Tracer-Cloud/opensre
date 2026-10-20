"""Cron expression parsing shared by the scheduler runner and every ``add`` command.

Two shapes are accepted: the standard five-field crontab line
(``minute hour day month day_of_week``) and a six-field line with a leading
seconds field (``second minute hour day month day_of_week``). The seconds form
exists for polling loops such as the CI repair loop, which must react within
half a minute; a five-field line can never fire more than once per minute.

Weekday numbers mean what they mean in crontab (0 and 7 are Sunday, 1 is
Monday), although APScheduler 3.x counts from 0 = Monday; every trigger is
built through :func:`build_cron_trigger`, which translates them. It also
expands a step after a name range (``mon-fri/2``, ``jan-dec/3``), which
APScheduler would otherwise silently drop.
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
    r"(?:(?P<every>\*)"
    r"|(?P<first>\d+)(?:-(?P<last>\d+))?"
    r"|(?P<first_name>[a-z]+)(?:-(?P<last_name>[a-z]+))?)"
    r"(?:/(?P<step>\d+))?"
)
#: Crontab month numbers by name; January is 1.
_MONTH_NUMBERS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_MONTH_NAME = re.compile(r"[a-z]+")


def day_of_week_names(field: str) -> str:
    """Spell a crontab ``day_of_week`` field in the day names APScheduler 3.x reads.

    Numeric items (``0``, ``1-5``, ``1-5/2``, ``*/2``) are read the crontab way
    and become names; a step is expanded into a list, whether it follows
    numbers or names (``mon-fri/2``), because APScheduler ignores a step
    written after a name range. Raises ``ValueError`` for a number above 7,
    an unknown name, a backwards range, a zero step, or an item APScheduler
    would only partly read (``mon-fri/-1``), so a schedule is rejected rather
    than silently run on other days.
    """
    days: set[int] = set()
    for item in field.lower().split(","):
        match = _CRONTAB_WEEKDAY_ITEM.fullmatch(item)
        if match is None:
            raise ValueError(f"unrecognized day_of_week item: {item!r}")
        days.update(_crontab_weekdays(item, match))
    return ",".join(_weekday_ranges(days))


def _crontab_weekdays(item: str, match: re.Match[str]) -> set[int]:
    """APScheduler weekday indexes for one numeric or named crontab item."""
    step = int(match["step"] or 1)
    if step < 1:
        raise ValueError(f"day_of_week step must be at least 1: {item!r}")
    named = match["first_name"] is not None
    first, last = (_named_weekday_span if named else _numeric_weekday_span)(item, match)
    if first > last:
        raise ValueError(f"day_of_week range runs backwards: {item!r}")
    days = range(first, last + 1, step)
    if named:
        return set(days)
    # Crontab 1 (Monday) is APScheduler 0; crontab 0 and 7 (Sunday) are both 6.
    return {(day - 1) % len(_WEEKDAY_NAMES) for day in days}


def _numeric_weekday_span(item: str, match: re.Match[str]) -> tuple[int, int]:
    """Crontab numbers (0-7) for the first and last day of a numeric item."""
    if match["every"]:
        return 0, _CRONTAB_LAST_WEEKDAY
    first = int(match["first"])
    if match["last"] is not None:
        last = int(match["last"])
    else:
        # ``N/step`` runs to the end of the week, like ``*/step``.
        last = first if match["step"] is None else _CRONTAB_LAST_WEEKDAY
    if max(first, last) > _CRONTAB_LAST_WEEKDAY:
        raise ValueError(f"day_of_week numbers run 0-7, with 0 and 7 for Sunday: {item!r}")
    return first, last


def _named_weekday_span(item: str, match: re.Match[str]) -> tuple[int, int]:
    """APScheduler indexes (0 = Monday) for the first and last day of a named item."""
    first = _weekday_index(match["first_name"], item)
    if match["last_name"] is not None:
        last = _weekday_index(match["last_name"], item)
    else:
        # ``mon/step`` runs to the end of the week, like ``*/step``.
        last = first if match["step"] is None else len(_WEEKDAY_NAMES) - 1
    return first, last


def _weekday_index(name: str, item: str) -> int:
    try:
        return _WEEKDAY_NAMES.index(name)
    except ValueError:
        raise ValueError(f"day_of_week names run mon-sun: {item!r}") from None


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


def _month_numbers(field: str) -> str:
    """Spell month names in a crontab ``month`` field as numbers (``jan-dec/3`` is ``1-12/3``).

    APScheduler reads a step after numbers but drops one after names. An
    unknown name passes through for APScheduler to reject.
    """
    return _MONTH_NAME.sub(_month_number, field.lower())


def _month_number(match: re.Match[str]) -> str:
    name = match.group()
    number = _MONTH_NUMBERS.get(name)
    return name if number is None else str(number)


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
            month=_month_numbers(month),
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


def _earliest_field_value(field: str, maximum: int) -> str:
    numbers = [int(match) for match in re.findall(r"\d+", field)]
    allowed = [number for number in numbers if number <= maximum]
    return str(min(allowed)) if allowed else "0"


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
    parts[0] = _earliest_field_value(parts[0], 59)
    capped = " ".join(parts)
    if _fires_more_than_hourly(capped, timezone):
        parts[1] = _earliest_field_value(parts[1], 23)
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
