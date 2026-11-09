"""Deterministic prioritization for durable work items."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from core.domain.work_items.models import WorkItem, WorkItemPriority, WorkItemStatus
from core.domain.work_items.schedule import parse_work_item_datetime

SECONDS_PER_HOUR: Final[float] = 3600.0
SECONDS_PER_DAY: Final[float] = 86400.0

PRIORITY_WEIGHTS: Final[dict[WorkItemPriority, int]] = {
    WorkItemPriority.URGENT: 90,
    WorkItemPriority.HIGH: 65,
    WorkItemPriority.NORMAL: 35,
    WorkItemPriority.LOW: 15,
}

STATUS_PENALTIES: Final[dict[WorkItemStatus, tuple[int, str]]] = {
    WorkItemStatus.BLOCKED: (-25, "blocked"),
    WorkItemStatus.DEFERRED: (-15, "deferred"),
}

COMPLETED_SCORE: Final[int] = -1
REMINDER_DUE_BOOST: Final[int] = 15
AGE_BOOST_AFTER_DAYS: Final[int] = 7
AGE_BOOST_CAP: Final[int] = 14
DEFAULT_PRIORITY_LIMIT: Final[int] = 5


@dataclass(frozen=True)
class ScoreContribution:
    """One additive (or overriding) term in a work-item score."""

    points: int
    reason: str


# (max hours remaining inclusive, contribution). First match wins; overdue is separate.
_DUE_WINDOWS: Final[tuple[tuple[float, ScoreContribution], ...]] = (
    (24.0, ScoreContribution(40, "due within 24h")),
    (72.0, ScoreContribution(25, "due within 3 days")),
    (168.0, ScoreContribution(10, "due this week")),
)
_OVERDUE: Final[ScoreContribution] = ScoreContribution(55, "overdue")


@dataclass(frozen=True)
class WorkItemScore:
    item: WorkItem
    score: int
    reasons: tuple[str, ...]


def score_work_item(item: WorkItem, *, now: datetime | None = None) -> WorkItemScore:
    if item.status is WorkItemStatus.COMPLETED:
        return WorkItemScore(item=item, score=COMPLETED_SCORE, reasons=("completed",))

    current = _aware_utc(now) if now is not None else datetime.now(UTC)
    contributions: list[ScoreContribution] = [
        ScoreContribution(PRIORITY_WEIGHTS[item.priority], f"{item.priority.value} priority"),
    ]

    due_at = _as_utc(item.due_at)
    if due_at is not None:
        due = _due_contribution(due_at, current)
        if due is not None:
            contributions.append(due)

    remind_at = _as_utc(item.remind_at)
    if remind_at is not None and remind_at <= current:
        contributions.append(ScoreContribution(REMINDER_DUE_BOOST, "reminder is due"))

    created_at = _as_utc(item.created_at)
    if created_at is not None:
        age = _age_contribution(created_at, current)
        if age is not None:
            contributions.append(age)

    penalty = STATUS_PENALTIES.get(item.status)
    if penalty is not None:
        points, reason = penalty
        contributions.append(ScoreContribution(points, reason))

    return WorkItemScore(
        item=item,
        score=sum(part.points for part in contributions),
        reasons=tuple(part.reason for part in contributions),
    )


def prioritize_work_items(
    items: Sequence[WorkItem],
    *,
    limit: int = DEFAULT_PRIORITY_LIMIT,
    now: datetime | None = None,
) -> list[WorkItemScore]:
    current = _aware_utc(now) if now is not None else None
    ranked = sorted(
        (score_work_item(item, now=current) for item in items if item.is_active),
        key=_rank_key,
    )
    return ranked[: max(limit, 0)]


def _rank_key(scored: WorkItemScore) -> tuple[int, int, str, str]:
    item = scored.item
    return (
        -scored.score,
        -PRIORITY_WEIGHTS[item.priority],
        item.created_at,
        item.title.lower(),
    )


def _due_contribution(due_at: datetime, now: datetime) -> ScoreContribution | None:
    hours_remaining = (due_at - now).total_seconds() / SECONDS_PER_HOUR
    if hours_remaining < 0:
        return _OVERDUE
    for max_hours, contribution in _DUE_WINDOWS:
        if hours_remaining <= max_hours:
            return contribution
    return None


def _age_contribution(created_at: datetime, now: datetime) -> ScoreContribution | None:
    age_days = int(max((now - created_at).total_seconds(), 0.0) // SECONDS_PER_DAY)
    if age_days < AGE_BOOST_AFTER_DAYS:
        return None
    return ScoreContribution(min(age_days, AGE_BOOST_CAP), f"open for {age_days}d")


def _as_utc(value: str) -> datetime | None:
    parsed = parse_work_item_datetime(value)
    if parsed is None:
        return None
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_PRIORITY_LIMIT",
    "PRIORITY_WEIGHTS",
    "ScoreContribution",
    "WorkItemScore",
    "prioritize_work_items",
    "score_work_item",
]
