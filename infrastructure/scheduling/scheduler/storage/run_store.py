"""SQLite-backed leased execution claims and run history."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import infrastructure.scheduling.scheduler.storage.database as database
from infrastructure.scheduling.scheduler.types import (
    DeliveryOutcome,
    Provider,
    TaskReport,
    TaskRun,
    TaskStatus,
)

logger = logging.getLogger(__name__)

#: How far back to look for a run with readable per-target history before
#: reporting none. Only a bound on work, not on correctness: exhausting it
#: reads as "history unknown", and a failed-only retry refuses to run rather
#: than widening to every destination.
_TARGETED_RUN_SCAN_LIMIT = 50
_RECOVERABLE_RUN_SCAN_LIMIT = 100
_CLAIM_LEASE_SECONDS = 30 * 60
_RUN_COLUMNS = (
    "task_id, fire_time, started_at, finished_at, status, posted_message_id, "
    "error, provider, targets, attempt, id, report, report_summary"
)


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    """Fenced identity for one scheduled execution attempt."""

    task_id: str
    fire_time: str
    attempt: int
    owner_token: str
    lease_expires_at: datetime
    target_filter: frozenset[tuple[Provider, str]] | None = None


@dataclass(frozen=True, slots=True)
class RecoverableRun:
    """Identity needed to resume one pending or expired scheduled execution."""

    task_id: str
    fire_time: str


def try_queue_run(task_id: str, fire_time: str, db_path: Path | None = None) -> bool:
    """Record a pending scheduler submission unless this tick already exists."""
    now_text = datetime.now(UTC).isoformat()
    with database.transaction(db_path, immediate=True) as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO task_runs "
            "(task_id, fire_time, started_at, status, target_filter) VALUES (?, ?, ?, ?, ?)",
            (task_id, fire_time, now_text, TaskStatus.PENDING.value, json.dumps(None)),
        )
        return cursor.rowcount == 1


def try_claim(
    task_id: str,
    fire_time: str,
    db_path: Path | None = None,
    *,
    target_filter: frozenset[tuple[Provider, str]] | None = None,
) -> ExecutionClaim | None:
    """Start pending/new work or reclaim an expired tick, excluding live runs of this task."""
    try:
        with database.transaction(db_path, immediate=True) as conn:
            now = datetime.now(UTC)
            now_text = now.isoformat()
            lease_text = (now + timedelta(seconds=_CLAIM_LEASE_SECONDS)).isoformat()
            if (
                conn.execute(
                    "SELECT 1 FROM task_runs WHERE task_id = ? AND status = ? "
                    "AND lease_expires_at >= ? LIMIT 1",
                    (task_id, TaskStatus.RUNNING.value, now_text),
                ).fetchone()
                is not None
            ):
                return None
            row = conn.execute(
                "SELECT attempt, status, lease_expires_at, target_filter FROM task_runs "
                "WHERE task_id = ? AND fire_time = ? ORDER BY attempt DESC LIMIT 1",
                (task_id, fire_time),
            ).fetchone()

            if row is not None:
                attempt = int(row[0])
                status = TaskStatus(row[1])
                lease = _parse_datetime(row[2])
                if status is not TaskStatus.PENDING:
                    if status is not TaskStatus.RUNNING or (lease is not None and lease >= now):
                        return None
                    conn.execute(
                        "UPDATE task_runs SET status = ?, finished_at = ?, error = ? "
                        "WHERE task_id = ? AND fire_time = ? AND attempt = ? AND status = ?",
                        (
                            TaskStatus.ABANDONED.value,
                            now_text,
                            "claim lease expired",
                            task_id,
                            fire_time,
                            attempt,
                            TaskStatus.RUNNING.value,
                        ),
                    )
                    attempt += 1
                stored_filter = _decode_target_filter(row[3])
                if status is TaskStatus.PENDING and target_filter is not None:
                    if stored_filter is not None:
                        target_filter = target_filter & stored_filter
                else:
                    target_filter = stored_filter
            else:
                attempt = 1

            owner_token = uuid4().hex
            conn.execute(
                "INSERT INTO task_runs "
                "(task_id, fire_time, attempt, started_at, status, owner_token, "
                "lease_expires_at, target_filter) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id, fire_time, attempt) DO UPDATE SET "
                "status = excluded.status, started_at = excluded.started_at, "
                "owner_token = excluded.owner_token, lease_expires_at = excluded.lease_expires_at, "
                "target_filter = excluded.target_filter",
                (
                    task_id,
                    fire_time,
                    attempt,
                    now_text,
                    TaskStatus.RUNNING.value,
                    owner_token,
                    lease_text,
                    json.dumps(sorted(target_filter) if target_filter is not None else None),
                ),
            )
            return ExecutionClaim(
                task_id,
                fire_time,
                attempt,
                owner_token,
                now + timedelta(seconds=_CLAIM_LEASE_SECONDS),
                target_filter,
            )
    except sqlite3.IntegrityError:
        return None


def claim_renewal_interval_seconds() -> float:
    """Return a renewal interval that leaves two intervals of lease safety."""
    return _CLAIM_LEASE_SECONDS / 3


def renew_claims(
    claims: Collection[ExecutionClaim],
    db_path: Path | None = None,
) -> dict[ExecutionClaim, datetime]:
    """Renew live fenced claims together and return their confirmed expiries."""
    if not claims:
        return {}
    renewed: dict[ExecutionClaim, datetime] = {}
    with database.transaction(db_path, immediate=True) as conn:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease_expires_at = now + timedelta(seconds=_CLAIM_LEASE_SECONDS)
        lease_text = lease_expires_at.isoformat()
        for claim in claims:
            cursor = conn.execute(
                "UPDATE task_runs SET lease_expires_at = ? "
                "WHERE task_id = ? AND fire_time = ? AND attempt = ? "
                "AND owner_token = ? AND status = ? AND lease_expires_at >= ?",
                (
                    lease_text,
                    claim.task_id,
                    claim.fire_time,
                    claim.attempt,
                    claim.owner_token,
                    TaskStatus.RUNNING.value,
                    now_text,
                ),
            )
            if cursor.rowcount == 1:
                renewed[claim] = lease_expires_at
    return renewed


def _decode_target_filter(raw: str) -> frozenset[tuple[Provider, str]] | None:
    """Read durable delivery scope; unknown or malformed scope never widens."""
    try:
        entries = json.loads(raw)
        if entries is None:
            return None
        if not isinstance(entries, list):
            raise ValueError("delivery scope must be a list")
        targets: set[tuple[Provider, str]] = set()
        for entry in entries:
            if (
                not isinstance(entry, list)
                or len(entry) != 2
                or not all(isinstance(value, str) for value in entry)
            ):
                raise ValueError("invalid delivery scope entry")
            targets.add((Provider(entry[0]), entry[1]))
        return frozenset(targets)
    except (ValueError, TypeError):
        logger.warning("Refusing recovery delivery with unreadable scope")
        return frozenset()


def get_recoverable_runs(
    *,
    limit: int = _RECOVERABLE_RUN_SCAN_LIMIT,
    db_path: Path | None = None,
    eligible_task_ids: Collection[str] | None = None,
) -> list[RecoverableRun]:
    """Return pending or expired ticks without live task owners, filtering before the limit."""
    if eligible_task_ids is not None and not eligible_task_ids:
        return []
    task_ids_json = json.dumps(list(eligible_task_ids)) if eligible_task_ids is not None else None
    with database.connection(db_path) as conn:
        now_text = datetime.now(UTC).isoformat()
        rows = conn.execute(
            "SELECT task_id, fire_time FROM task_runs AS current "
            "WHERE (current.status = ? OR (current.status = ? "
            "AND current.lease_expires_at != '' AND current.lease_expires_at < ?)) "
            "AND NOT EXISTS (SELECT 1 FROM task_runs AS live "
            "WHERE live.task_id = current.task_id AND live.status = ? "
            "AND live.lease_expires_at >= ?) "
            "AND (? IS NULL OR current.task_id IN (SELECT value FROM json_each(?))) "
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
                limit,
            ),
        ).fetchall()
        return [RecoverableRun(task_id=str(row[0]), fire_time=str(row[1])) for row in rows]


def complete_run(
    claim: ExecutionClaim,
    *,
    status: TaskStatus,
    posted_message_id: str = "",
    error: str = "",
    provider: str = "",
    targets: Sequence[DeliveryOutcome] = (),
    db_path: Path | None = None,
) -> bool:
    """Mark a claimed run as completed, recording each destination's outcome.

    ``targets`` is stored in the order it is given, which is the order the run
    planned its destinations in — not the order they finished.
    """
    with database.transaction(db_path, immediate=True) as conn:
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "UPDATE task_runs SET finished_at = ?, status = ?, "
            "posted_message_id = ?, error = ?, provider = ?, targets = ? "
            "WHERE task_id = ? AND fire_time = ? AND attempt = ? "
            "AND owner_token = ? AND status = ? AND lease_expires_at >= ?",
            (
                now,
                status.value,
                posted_message_id,
                error,
                provider,
                _encode_targets(targets),
                claim.task_id,
                claim.fire_time,
                claim.attempt,
                claim.owner_token,
                TaskStatus.RUNNING.value,
                now,
            ),
        )
        completed = cursor.rowcount == 1
        if not completed:
            logger.warning(
                "Completion rejected for reclaimed task %s fire_time=%s attempt=%d",
                claim.task_id,
                claim.fire_time,
                claim.attempt,
            )
        return completed


def record_run_report(claim: ExecutionClaim, report: str, db_path: Path | None = None) -> bool:
    """Retain a built report before delivery, only while this attempt owns its lease."""
    with database.transaction(db_path, immediate=True) as conn:
        cursor = conn.execute(
            "UPDATE task_runs SET report = ?, report_summary = ? "
            "WHERE task_id = ? AND fire_time = ? AND attempt = ? "
            "AND owner_token = ? AND status = ? AND lease_expires_at >= ?",
            (
                report,
                report.summary if isinstance(report, TaskReport) else "",
                claim.task_id,
                claim.fire_time,
                claim.attempt,
                claim.owner_token,
                TaskStatus.RUNNING.value,
                datetime.now(UTC).isoformat(),
            ),
        )
        return cursor.rowcount == 1


def _encode_targets(targets: Sequence[DeliveryOutcome]) -> str:
    """Serialize per-destination outcomes for the ``targets`` column."""
    if not targets:
        return ""
    return json.dumps([outcome.model_dump(mode="json") for outcome in targets])


def _decode_targets(raw: Any) -> tuple[DeliveryOutcome, ...]:
    """Read back per-destination outcomes; unreadable rows degrade to empty."""
    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        entries = json.loads(text)
        return tuple(DeliveryOutcome.model_validate(entry) for entry in entries)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Skipping unreadable per-target run outcomes", exc_info=True)
        return ()


def _row_to_task_run(row: tuple[Any, ...]) -> TaskRun:
    return TaskRun(
        task_id=row[0],
        fire_time=row[1],
        started_at=row[2],
        finished_at=row[3] or None,
        status=TaskStatus(row[4]),
        posted_message_id=row[5] or "",
        error=row[6] or "",
        provider=row[7] or "",
        targets=_decode_targets(row[8]),
        attempt=int(row[9] or 1),
        run_id=int(row[10]),
        report=row[11],
        report_summary=row[12] or "",
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def get_runs(task_id: str, limit: int = 20, db_path: Path | None = None) -> list[TaskRun]:
    """Return recent runs for a task, newest first."""
    with database.connection(db_path) as conn:
        cursor = conn.execute(
            f"SELECT {_RUN_COLUMNS} "
            "FROM task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        return [_row_to_task_run(row) for row in cursor.fetchall()]


def get_latest_runs(task_ids: Collection[str], db_path: Path | None = None) -> dict[str, TaskRun]:
    """Read the latest attempt for each requested task in one snapshot."""
    if not task_ids:
        return {}
    with database.connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM task_runs WHERE id IN ("
            "SELECT (SELECT id FROM task_runs WHERE task_id = requested.value "
            "ORDER BY started_at DESC, id DESC LIMIT 1) FROM json_each(?) AS requested)",
            (json.dumps(list(task_ids)),),
        ).fetchall()
        runs = [_row_to_task_run(row) for row in rows]
        return {run.task_id: run for run in runs}


def get_group_runs(
    task_ids: Collection[str], *, limit: int = 20, db_path: Path | None = None
) -> list[TaskRun]:
    """Return recent attempts across a loop's tasks, newest first."""
    if not task_ids or limit <= 0:
        return []
    with database.connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM task_runs "
            "WHERE task_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            (json.dumps(list(task_ids)), limit),
        ).fetchall()
        return [_row_to_task_run(row) for row in rows]


def get_group_run(
    task_ids: Collection[str], run_id: int, *, db_path: Path | None = None
) -> TaskRun | None:
    """Resolve a stable run ID only within the requested loop's tasks."""
    with database.connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM task_runs "
            "WHERE id = ? AND task_id IN (SELECT value FROM json_each(?))",
            (run_id, json.dumps(list(task_ids))),
        ).fetchone()
        return _row_to_task_run(row) if row is not None else None


def get_latest_run_for_fire_time(
    task_id: str, fire_time: str, db_path: Path | None = None
) -> TaskRun | None:
    """Return the newest attempt recorded for one task fire time."""
    with database.connection(db_path) as conn:
        cursor = conn.execute(
            f"SELECT {_RUN_COLUMNS} "
            "FROM task_runs WHERE task_id = ? AND fire_time = ? "
            "ORDER BY attempt DESC LIMIT 1",
            (task_id, fire_time),
        )
        row = cursor.fetchone()
        return _row_to_task_run(row) if row is not None else None


def get_latest_finished_run(task_id: str, db_path: Path | None = None) -> TaskRun | None:
    """Return the most recently completed run for ``task_id``, if any.

    Orders by completion time, not start time, and ignores in-flight rows so a
    burst of pending claims cannot hide the last delivery outcome.
    """
    with database.connection(db_path) as conn:
        cursor = conn.execute(
            f"SELECT {_RUN_COLUMNS} "
            "FROM task_runs WHERE task_id = ? AND status IN (?, ?) "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
            (task_id, TaskStatus.SUCCESS.value, TaskStatus.FAILED.value),
        )
        row = cursor.fetchone()
        return _row_to_task_run(row) if row is not None else None


def get_latest_targeted_run(task_id: str, db_path: Path | None = None) -> TaskRun | None:
    """Return the most recent completed run that recorded per-target outcomes.

    Skips runs with no usable per-target history — one that failed before
    delivery (message build), one whose retry matched no destination, or one
    whose stored outcomes cannot be decoded. Those carry no information about
    what was delivered, so letting one shadow an earlier partial failure would
    strand a ``--failed-only`` retry: either widened back to every destination
    (re-posting where the message already landed) or narrowed to nothing.

    Emptiness is judged after decoding, not by the raw column: a non-empty but
    unreadable value decodes to no outcomes, so a SQL-level check alone would
    let it shadow a readable older run.
    """
    with database.connection(db_path) as conn:
        cursor = conn.execute(
            f"SELECT {_RUN_COLUMNS} "
            "FROM task_runs WHERE task_id = ? AND status IN (?, ?) "
            "AND targets IS NOT NULL AND targets != '' "
            "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT ?",
            (
                task_id,
                TaskStatus.SUCCESS.value,
                TaskStatus.FAILED.value,
                _TARGETED_RUN_SCAN_LIMIT,
            ),
        )
        for row in cursor.fetchall():
            run = _row_to_task_run(row)
            if run.targets:
                return run
        return None


def delete_runs(task_id: str, db_path: Path | None = None) -> int:
    """Delete all task-run records for a given task ID.

    Returns the number of deleted rows. Safe to call when no DB or table
    exists (returns 0). Idempotent — subsequent calls return 0.
    """
    path = db_path or database.default_run_database_path()
    if not path.exists():
        return 0
    with database.transaction(path) as conn:
        cursor = conn.execute(
            "DELETE FROM task_runs WHERE task_id = ?",
            (task_id,),
        )
        return cursor.rowcount


__all__ = [
    "claim_renewal_interval_seconds",
    "complete_run",
    "delete_runs",
    "RecoverableRun",
    "ExecutionClaim",
    "get_recoverable_runs",
    "get_latest_run_for_fire_time",
    "get_latest_finished_run",
    "get_latest_targeted_run",
    "get_runs",
    "get_group_run",
    "get_group_runs",
    "get_latest_runs",
    "record_run_report",
    "renew_claims",
    "try_claim",
    "try_queue_run",
]
