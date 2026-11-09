"""Unit tests for durable work-item prioritization."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from core.domain.work_items.models import (
    WorkItem,
    WorkItemPriority,
    WorkItemStatus,
    make_work_item,
)
from core.domain.work_items.scoring import prioritize_work_items, score_work_item


def _item(
    *,
    title: str = "task",
    priority: WorkItemPriority = WorkItemPriority.NORMAL,
    status: WorkItemStatus = WorkItemStatus.OPEN,
    due_at: str = "",
    remind_at: str = "",
    created_at: str = "2026-01-01T00:00:00+00:00",
) -> WorkItem:
    item = make_work_item(title=title, priority=priority, due_at=due_at, remind_at=remind_at)
    return replace(
        item,
        status=status,
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at if status is WorkItemStatus.COMPLETED else "",
    )


def test_urgent_outranks_low_without_deadlines() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    created = now.isoformat()
    urgent = score_work_item(
        _item(title="u", priority=WorkItemPriority.URGENT, created_at=created),
        now=now,
    )
    low = score_work_item(
        _item(title="l", priority=WorkItemPriority.LOW, created_at=created),
        now=now,
    )
    assert urgent.score > low.score
    assert urgent.reasons == ("urgent priority",)


def test_overdue_beats_same_priority_future_due() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    overdue = score_work_item(
        _item(due_at=(now - timedelta(hours=2)).isoformat()),
        now=now,
    )
    later = score_work_item(
        _item(due_at=(now + timedelta(days=5)).isoformat()),
        now=now,
    )
    assert overdue.score > later.score
    assert "overdue" in overdue.reasons


def test_blocked_and_deferred_apply_penalties() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    created = now.isoformat()
    open_item = score_work_item(_item(status=WorkItemStatus.OPEN, created_at=created), now=now)
    blocked = score_work_item(_item(status=WorkItemStatus.BLOCKED, created_at=created), now=now)
    deferred = score_work_item(_item(status=WorkItemStatus.DEFERRED, created_at=created), now=now)
    assert blocked.score == open_item.score - 25
    assert deferred.score == open_item.score - 15
    assert "blocked" in blocked.reasons
    assert "deferred" in deferred.reasons


def test_completed_is_sentinel_and_excluded_from_prioritize() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    completed = _item(title="done", status=WorkItemStatus.COMPLETED)
    open_item = _item(title="open", priority=WorkItemPriority.LOW)
    scored = score_work_item(completed, now=now)
    assert scored.score == -1
    assert scored.reasons == ("completed",)
    ranked = prioritize_work_items([completed, open_item], limit=5, now=now)
    assert [entry.item.title for entry in ranked] == ["open"]


def test_prioritize_orders_by_score_then_priority_then_created() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    # Keep age below the boost threshold so priority alone decides ranking.
    older_high = _item(
        title="older",
        priority=WorkItemPriority.HIGH,
        created_at=(now - timedelta(days=2)).isoformat(),
    )
    newer_high = _item(
        title="newer",
        priority=WorkItemPriority.HIGH,
        created_at=(now - timedelta(days=1)).isoformat(),
    )
    low = _item(
        title="low",
        priority=WorkItemPriority.LOW,
        created_at=now.isoformat(),
    )
    ranked = prioritize_work_items([low, newer_high, older_high], limit=3, now=now)
    assert [entry.item.title for entry in ranked] == ["older", "newer", "low"]


def test_reminder_due_and_age_boost() -> None:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    with_reminder = score_work_item(
        _item(
            remind_at=(now - timedelta(minutes=5)).isoformat(),
            created_at=(now - timedelta(days=10)).isoformat(),
        ),
        now=now,
    )
    assert "reminder is due" in with_reminder.reasons
    assert "open for 10d" in with_reminder.reasons
    assert with_reminder.score == 35 + 15 + 10
