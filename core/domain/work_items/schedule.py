"""Scheduling helpers for work-item reminders."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_work_item_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC)


def resolve_work_item_datetime(value: str, timezone: str) -> datetime | None:
    """Resolve a work-item datetime to an aware instant using its IANA timezone."""
    parsed = parse_work_item_datetime(value)
    if parsed is None:
        return None
    try:
        zone = ZoneInfo(timezone.strip())
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"invalid IANA timezone: {timezone!r}") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(UTC).astimezone(zone)
        if round_trip.replace(tzinfo=None) == parsed:
            return candidate
    return None


def cron_from_datetime(value: datetime) -> str:
    return f"{value.minute} {value.hour} {value.day} {value.month} *"


__all__ = [
    "cron_from_datetime",
    "parse_work_item_datetime",
    "resolve_work_item_datetime",
]
