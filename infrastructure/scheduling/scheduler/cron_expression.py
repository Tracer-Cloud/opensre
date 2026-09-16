"""Cron expression parsing shared by the scheduler runner and every ``add`` command.

Two shapes are accepted: the standard five-field crontab line
(``minute hour day month day_of_week``) and a six-field line with a leading
seconds field (``second minute hour day month day_of_week``). The seconds form
exists for polling loops such as the CI repair loop, which must react within
half a minute; a five-field line can never fire more than once per minute.
"""

from __future__ import annotations

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


def build_cron_trigger(cron: str, timezone: str | None) -> Any:
    """Build an APScheduler ``CronTrigger`` for a five- or six-field expression.

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
            day_of_week=day_of_week,
            timezone=timezone,
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"invalid cron expression or timezone: {exc}") from exc


__all__ = [
    "CRON_FIELD_COUNT",
    "CRON_FIELD_COUNT_ERROR",
    "CRON_FIELD_COUNT_WITH_SECONDS",
    "CRON_FORMAT_HELP",
    "build_cron_trigger",
]
