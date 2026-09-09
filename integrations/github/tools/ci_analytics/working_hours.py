"""Working hours: the part of a wall-clock wait a developer actually sat through."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.runtime_metadata.probes import local_tz_name

_WEEKDAY_COUNT = 5


@dataclass(frozen=True)
class WorkingHours:
    """A weekday working window in one timezone; the default is 09:00 to 18:00 UTC."""

    timezone: str = "UTC"
    start_hour: int = 9
    end_hour: int = 18
    weekdays_only: bool = True

    @property
    def label(self) -> str:
        days = "Mon-Fri" if self.weekdays_only else "every day"
        return f"{days} {self.start_hour:02d}:00-{self.end_hour:02d}:00 {self.timezone}"

    def minutes(self, start: datetime, end: datetime) -> float:
        """Minutes of ``[start, end)`` that fall inside the working window."""
        if end <= start:
            return 0.0
        zone = ZoneInfo(self.timezone)
        cursor = start.astimezone(zone)
        finish = end.astimezone(zone)
        total = 0.0
        while cursor < finish:
            day_start = cursor.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
            day_end = cursor.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)
            next_day = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if not self.weekdays_only or cursor.weekday() < _WEEKDAY_COUNT:
                window_start = max(cursor, day_start)
                window_end = min(finish, day_end)
                if window_end > window_start:
                    total += (window_end - window_start).total_seconds() / 60
            cursor = next_day
        return total


def local_timezone() -> str:
    """This machine's IANA timezone, or UTC when it cannot be resolved."""
    name = local_tz_name()
    try:
        ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        return "UTC"
    return name


def local_working_hours() -> WorkingHours:
    return WorkingHours(timezone=local_timezone())


__all__ = ["WorkingHours", "local_timezone", "local_working_hours"]
