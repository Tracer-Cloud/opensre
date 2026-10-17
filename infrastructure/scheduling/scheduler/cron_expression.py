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
# Named weekday range with an optional step, e.g. ``mon-fri/2`` or ``sat-sun``.
_NAMED_WEEKDAY_RANGE = re.compile(
    r"(?P<first>[a-z]{3})(?:-(?P<last>[a-z]{3}))?(?:/(?P<step>\d+))?$"
)

#: APScheduler/crontab month names (1-indexed: jan=1 … dec=12).
_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# Named month range with an optional step, e.g. ``jan-dec/3`` or ``apr-jun``.
_NAMED_MONTH_RANGE = re.compile(r"(?P<first>[a-z]{3})(?:-(?P<last>[a-z]{3}))?(?:/(?P<step>\d+))?$")


def _named_weekday_to_numeric(item: str) -> str | None:
    """Convert a named weekday item to its crontab-numeric equivalent, or return None.

    ``mon-fri/2`` → ``1-5/2``, ``sun-sat/3`` → ``0-6/3``, ``mon`` → ``1``.
    Sunday is 0 at the start of a range and 7 at the end so ``mon-sun/2``
    becomes ``1-7/2``.
    Returns None when the name is unrecognised (passes through to APScheduler).
    """
    match = _NAMED_WEEKDAY_RANGE.fullmatch(item)
    if match is None:
        return None
    first_name = match["first"]
    last_name = match["last"]
    step_str = match["step"]
    try:
        first_idx = _WEEKDAY_NAMES.index(first_name)
    except ValueError:
        return None
    first_crontab = first_idx + 1  # mon=1, sun=7
    if last_name is None:
        numeric = str(first_crontab % 8)  # sun → 0 when standalone
        return f"{numeric}/{step_str}" if step_str else numeric
    try:
        last_idx = _WEEKDAY_NAMES.index(last_name)
    except ValueError:
        return None
    last_crontab = last_idx + 1  # mon=1, sun=7
    numeric_range = f"{first_crontab % 8}-{last_crontab}"
    return f"{numeric_range}/{step_str}" if step_str else numeric_range


def _named_month_to_numeric(item: str) -> str | None:
    """Convert a named month item to its numeric equivalent, or return None.

    ``jan-dec/3`` → ``1-12/3``, ``apr-jun`` → ``4-6``, ``jan`` → ``1``.
    Returns None when the name is unrecognised.
    """
    match = _NAMED_MONTH_RANGE.fullmatch(item)
    if match is None:
        return None
    first_name = match["first"]
    last_name = match["last"]
    step_str = match["step"]
    try:
        first_num = _MONTH_NAMES.index(first_name) + 1
    except ValueError:
        return None
    if last_name is None:
        numeric = str(first_num)
        return f"{numeric}/{step_str}" if step_str else numeric
    try:
        last_num = _MONTH_NAMES.index(last_name) + 1
    except ValueError:
        return None
    numeric_range = f"{first_num}-{last_num}"
    return f"{numeric_range}/{step_str}" if step_str else numeric_range


def day_of_week_names(field: str) -> str:
    """Spell a crontab ``day_of_week`` field in the day names APScheduler 3.x reads.

    Numeric items (``0``, ``1-5``, ``1-5/2``, ``*/2``) are read the crontab way
    and become names; a step is expanded into a list, because APScheduler
    ignores a step written after a name range. Named ranges with a step
    (``mon-fri/2``) are first translated to their numeric equivalents so the
    same expansion applies. Names and a bare ``*`` mean the same to both and
    pass through. Raises ``ValueError`` for a number above 7, a backwards
    range, or a zero step.
    """
    passthrough: list[str] = []
    days: set[int] = set()
    for item in field.lower().split(","):
        numeric = _named_weekday_to_numeric(item)
        if numeric is not None:
            item = numeric
        match = _CRONTAB_WEEKDAY_ITEM.fullmatch(item)
        if match is None or item == "*":
            passthrough.append(item)
        else:
            days.update(_crontab_weekdays(item, match))
    return ",".join([*_weekday_ranges(days), *passthrough])


def month_field(field: str) -> str:
    """Spell a crontab ``month`` field so APScheduler 3.x honours steps in named ranges.

    APScheduler ignores a step written after a name range (``jan-dec/3`` runs
    every month). Translate named range items to their numeric equivalents so
    the step is applied correctly. Items without a step pass through unchanged.
    """
    out: list[str] = []
    for item in field.lower().split(","):
        numeric = _named_month_to_numeric(item)
        if numeric is not None and "/" in item:
            out.append(numeric)
        else:
            out.append(item)
    return ",".join(out)


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
            month=month_field(month),
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
    "month_field",
    "day_of_week_names",
]
