"""Recovery queries for registered tasks plus exact transient ownership keys."""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import UTC, datetime

from infrastructure.scheduling.scheduler.storage import database
from infrastructure.scheduling.scheduler.storage.run_store import RecoverableRun
from infrastructure.scheduling.scheduler.types import TaskStatus


def get_recoverable_runs_for_scope(
    eligible_task_ids: Collection[str],
    exact_run_keys: Collection[tuple[str, str]],
    *,
    limit: int,
) -> list[RecoverableRun]:
    """Return recoverable rows owned by task ID or exact durable key before limiting.

    Recurring ownership is task-wide while a DateTrigger ownership hint is scoped
    to one exact ``(task_id, fire_time)``. Filtering both forms inside SQL before
    ``LIMIT`` prevents older rows for a moved one-shot task from hiding its admitted
    key or unrelated registered work, while retaining the normal live-owner and
    expired-lease recovery rules.
    """
    if limit <= 0 or (not eligible_task_ids and not exact_run_keys):
        return []

    task_ids_json = json.dumps(sorted(set(eligible_task_ids))) if eligible_task_ids else None
    run_keys_json = (
        json.dumps(
            sorted({(str(task_id), str(fire_time)) for task_id, fire_time in exact_run_keys})
        )
        if exact_run_keys
        else None
    )
    now_text = datetime.now(UTC).isoformat()

    with database.connection() as conn:
        rows = conn.execute(
            "SELECT current.task_id, current.fire_time FROM task_runs AS current "
            "WHERE (current.status = ? OR (current.status = ? "
            "AND current.lease_expires_at != '' AND current.lease_expires_at < ?)) "
            "AND NOT EXISTS (SELECT 1 FROM task_runs AS live "
            "WHERE live.task_id = current.task_id AND live.status = ? "
            "AND live.lease_expires_at >= ?) "
            "AND ((? IS NOT NULL AND current.task_id IN (SELECT value FROM json_each(?))) "
            "OR (? IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(?) AS owned "
            "WHERE json_extract(owned.value, '$[0]') = current.task_id "
            "AND json_extract(owned.value, '$[1]') = current.fire_time))) "
            "AND current.attempt = (SELECT MAX(latest.attempt) FROM task_runs AS latest "
            "WHERE latest.task_id = current.task_id "
            "AND latest.fire_time = current.fire_time) "
            "ORDER BY current.started_at, current.task_id, current.fire_time LIMIT ?",
            (
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                now_text,
                TaskStatus.RUNNING.value,
                now_text,
                task_ids_json,
                task_ids_json,
                run_keys_json,
                run_keys_json,
                limit,
            ),
        ).fetchall()

    return [RecoverableRun(task_id=str(row[0]), fire_time=str(row[1])) for row in rows]


__all__ = ["get_recoverable_runs_for_scope"]
