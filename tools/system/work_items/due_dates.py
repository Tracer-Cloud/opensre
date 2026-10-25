"""Resolve and apply date filters without changing persisted work-item dates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.domain.work_items import (
    AmbiguousWorkItemDatetimeError,
    WorkItem,
    resolve_work_item_datetime,
)


@dataclass(frozen=True)
class DueDateFilter:
    """An inclusive lower bound, exclusive upper bound, and optional overdue cutoff."""

    after: datetime | None
    before: datetime | None
    overdue_as_of: datetime | None
    timezone: str

    def summary(self) -> dict[str, str | None]:
        """Return the resolved instants used to calculate this response's count."""
        return {
            "due_after": self.after.isoformat() if self.after else None,
            "due_before": self.before.isoformat() if self.before else None,
            "overdue_as_of": self.overdue_as_of.isoformat() if self.overdue_as_of else None,
            "timezone": self.timezone,
        }


def _resolve_bound(value: str, field: str, timezone: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        resolved = resolve_work_item_datetime(value, timezone)
    except AmbiguousWorkItemDatetimeError as exc:
        raise ValueError(f"{field} is ambiguous; include an explicit UTC offset") from exc
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a resolvable ISO date/time") from exc
    if resolved is None:
        raise ValueError(f"{field} must be an ISO date/time that exists in the specified timezone")
    return resolved.astimezone(UTC)


def parse_due_date_filter(
    *,
    due_after: str = "",
    due_before: str = "",
    overdue_only: bool = False,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> DueDateFilter | None:
    """Validate bounds before reading the store; no criteria leaves listings unchanged."""
    if not isinstance(due_after, str) or not isinstance(due_before, str):
        raise ValueError("due_after and due_before must be ISO date/time strings")
    if not isinstance(overdue_only, bool):
        raise ValueError("overdue_only must be a boolean")
    if not due_after.strip() and not due_before.strip() and not overdue_only:
        return None
    try:
        if not isinstance(timezone, str):
            raise ValueError("timezone must be a valid IANA timezone")
        zone = ZoneInfo(timezone.strip())
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    after = _resolve_bound(due_after, "due_after", zone.key)
    before = _resolve_bound(due_before, "due_before", zone.key)
    if after is not None and before is not None and after >= before:
        raise ValueError("due_after must be earlier than due_before")
    cutoff = (now or datetime.now(UTC)).astimezone(UTC) if overdue_only else None
    return DueDateFilter(after=after, before=before, overdue_as_of=cutoff, timezone=zone.key)


def filter_due_dates(
    items: Sequence[WorkItem], criteria: DueDateFilter
) -> tuple[list[WorkItem], int]:
    """Preserve order while excluding undated rows and counting unresolvable dates."""
    matched: list[WorkItem] = []
    invalid = 0
    for item in items:
        if not item.due_at.strip():
            continue
        try:
            due = resolve_work_item_datetime(item.due_at, criteria.timezone)
        except (ValueError, OverflowError):
            due = None
        if due is None:
            invalid += 1
            continue
        if criteria.after is not None and due < criteria.after:
            continue
        if criteria.before is not None and due >= criteria.before:
            continue
        if criteria.overdue_as_of is not None and (
            not item.is_active or due >= criteria.overdue_as_of
        ):
            continue
        matched.append(item)
    return matched, invalid


__all__ = ["DueDateFilter", "filter_due_dates", "parse_due_date_filter"]
