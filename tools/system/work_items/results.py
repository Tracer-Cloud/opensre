"""Result shaping for work-item tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.domain.work_items import CompleteWorkItemsResult, WorkItem, WorkItemScore, work_items_path


def item_summary(item: WorkItem, *, include_notes: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item.id,
        "display_id": item.display_id,
        "title": item.title,
        "status": item.status.value,
        "priority": item.priority.value,
        "project": item.project,
        "owner": item.owner,
        "due_at": item.due_at,
        "remind_at": item.remind_at,
        "channel": item.channel.to_dict(),
        "channel_targets": [target.to_dict() for target in item.channel_targets],
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "completed_at": item.completed_at,
    }
    if include_notes and item.notes:
        payload["notes"] = item.notes[:1000]
    return payload


def added_result(item: WorkItem, *, scheduled_task_id: str = "") -> dict[str, Any]:
    return {
        "status": "created",
        "task": item_summary(item, include_notes=True),
        "scheduled_task_id": scheduled_task_id,
        "path": str(work_items_path()),
    }


def list_result(
    items: Sequence[WorkItem], *, total: int, offset: int, limit: int
) -> dict[str, Any]:
    """Describe a filtered page and expose a continuation only when it advances."""
    returned = len(items)
    has_more = offset + returned < total
    return {
        "tasks": [item_summary(item) for item in items],
        "returned": returned,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": offset + returned if returned and has_more else None,
        "path": str(work_items_path()),
    }


def complete_result(result: CompleteWorkItemsResult) -> dict[str, Any]:
    return {
        "status": "completed" if result.completed else "no_change",
        "completed": [item_summary(item) for item in result.completed],
        "already_completed": [item_summary(item) for item in result.already_completed],
        "not_found": list(result.not_found),
        "ambiguous": {
            selector: [item_summary(item) for item in candidates]
            for selector, candidates in result.ambiguous.items()
        },
    }


def update_result(item: WorkItem) -> dict[str, Any]:
    return {"status": "updated", "task": item_summary(item, include_notes=True)}


def priority_result(scores: Sequence[WorkItemScore]) -> dict[str, Any]:
    return {
        "recommendations": [
            {
                "rank": index,
                "score": scored.score,
                "reasons": list(scored.reasons),
                "task": item_summary(scored.item),
            }
            for index, scored in enumerate(scores, start=1)
        ]
    }


def scheduled_result(
    task_id: str,
    *,
    cron: str,
    timezone: str,
    provider: str,
    chat_id: str,
    delivery_targets: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    return {
        "status": "scheduled",
        "scheduled_task_id": task_id,
        "cron": cron,
        "timezone": timezone,
        "provider": provider,
        "chat_id": chat_id,
        "delivery_targets": list(delivery_targets),
    }


__all__ = [
    "added_result",
    "complete_result",
    "item_summary",
    "list_result",
    "priority_result",
    "scheduled_result",
    "update_result",
]
