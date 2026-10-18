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
#: Three-letter month and weekday names, lowercase. APScheduler 3.x reads both,
#: but its name-range expression stops at the name and silently drops a trailing
#: step: ``mon-fri/2`` fires every weekday, ``jan-dec/3`` fires every month.
_MONTH_NAMES = (
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)
#: Crontab weekday names, indexed from 0. Both 0 and the name ``sun`` are Sunday.
_WEEKDAY_ORDER = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_NAMED_RANGE_WITH_STEP = re.compile(
    r"(?P<first>[a-z]{3})(?:-(?P<last>[a-z]{3}))?/(?P<step>\d+)",
    re.IGNORECASE,
)


def day_of_week_names(field: str) -> str:
    """Spell a crontab ``day_of_week`` field in the day names APScheduler 3.x reads.

    Numeric items (``0``, ``1-5``, ``1-5/2``, ``*/2``) are read the crontab way
    and become names; a step is expanded into a list, because APScheduler
    ignores a step written after a name range. A named step (``mon-fri/2``)
    is the same list: the names become crontab numbers, then that numeric
    rule selects them. Names without a step and a bare ``*`` mean the same
    to both and pass through. Raises ``ValueError`` for a number above 7,
    an unknown name, a backwards range, or a zero step.
    """
    passthrough: list[str] = []
    days: set[int] = set()
    for item in field.lower().split(","):
        named = _NAMED_RANGE_WITH_STEP.fullmatch(item)
        match = _CRONTAB_WEEKDAY_ITEM.fullmatch(item)
        if named:
            days.update(_named_weekdays(item, named))
        elif match is None or item == "*":
            passthrough.append(item)
        else:
            days.update(_crontab_weekdays(item, match))
    return ",".join([*_weekday_ranges(days), *passthrough])


def month_names(field: str) -> str:
    """Spell a crontab ``month`` field so a step after a name range survives.

    ``1-12/3`` already works, so numeric items pass through. A named step
    (``jan-dec/3``, ``feb-nov/3``) becomes that same numeric range and is
    expanded with the month rule, because APScheduler 3.x matches the names
    and discards the step instead of rejecting the field. A bare name or
    name range (``jan``, ``jan-mar``) means the same to both and passes
    through. Raises ``ValueError`` for an unknown name, a backwards range,
    or a zero step.
    """
    items: list[str] = []
    for item in field.lower().split(","):
        match = _NAMED_RANGE_WITH_STEP.fullmatch(item)
        if match is None:
            items.append(item)
            continue
        items.append(",".join(_named_steps(("", *_MONTH_NAMES), item, match)))
    return ",".join(items)


def _named_weekdays(item: str, match: re.Match[str]) -> set[int]:
    """APScheduler weekday indexes for one ``mon-fri/2`` style item.

    The names become crontab numbers, Sunday first, and :func:`_crontab_weekdays`
    selects them, so ``mon-fri/2`` is ``1-5/2``. Sunday is also crontab 7, so a
    range that ends on Sunday (``fri-sun``) is written ``N-7`` with the same
    step; index 0 would make it look backwards. ``fri/2`` runs to the end of
    the week, like ``5/2``. A range that runs backwards (``fri-mon``) is
    rejected, the same way a numeric range is.
    """
    if match["last"] == "sun" and match["first"] != "sun":
        first = _WEEKDAY_ORDER.index(match["first"])
        end = _CRONTAB_LAST_WEEKDAY
        written = _CRONTAB_WEEKDAY_ITEM.fullmatch(f"{first}-{end}/{int(match['step'])}")
        return _crontab_weekdays(item, written)
    return _crontab_weekdays(item, _named_as_crontab(item, match, _WEEKDAY_ORDER))


def _named_steps(names: tuple[str, ...], item: str, match: re.Match[str]) -> list[str]:
    """Names selected by one ``name/step`` or ``name-name/step`` item.

    ``names`` is numbered from its start, so a leading ``""`` makes months
    1-based, the way ``1-12/3`` is numbered. The numeric range rule then
    picks every step-th name. A backwards range or a zero step is rejected.
    """
    numeric = _named_as_crontab(item, match, names)
    first = int(numeric["first"])
    last = int(numeric["last"]) if numeric["last"] is not None else len(names) - 1
    return [names[index] for index in range(first, last + 1, int(numeric["step"]))]


def _named_as_crontab(item: str, match: re.Match[str], names: tuple[str, ...]) -> re.Match[str]:
    """The numeric crontab item a positive-order ``name/step`` or ``name-name/step`` is.

    Names are numbered from the start of ``names``: months from 1, weekdays
    from 0 with Sunday first. ``name/step`` has no end, like ``N/step``.
    A range that runs backwards is left to the caller; this only writes the
    positive numeric form the existing rule already expands.
    """
    step = int(match["step"])
    try:
        first = names.index(match["first"])
    except ValueError:
        raise ValueError(f"unknown name in {item!r}") from None
    last = None
    if match["last"] is not None:
        try:
            last = names.index(match["last"])
        except ValueError:
            raise ValueError(f"unknown name in {item!r}") from None
        if first > last:
            raise ValueError(f"range runs backwards: {item!r}")
    if step < 1:
        raise ValueError(f"step must be at least 1: {item!r}")
    written = f"{first}-{last}/{step}" if last is not None else f"{first}/{step}"
    numeric = _CRONTAB_WEEKDAY_ITEM.fullmatch(written)
    if numeric is None:  # pragma: no cover - written from a matched name step
        raise ValueError(f"invalid name step: {item!r}")
    return numeric


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
            month=month_names(month),
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
    "month_names",
]
