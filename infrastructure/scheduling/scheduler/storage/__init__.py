"""Scheduler task definitions and execution history persistence."""

import sqlite3
from dataclasses import replace
from pathlib import Path

from infrastructure.scheduling.scheduler.storage.database import (
    default_run_database_path,
    run_database_path,
)
from infrastructure.scheduling.scheduler.storage.run_store import (
    BacklogSnapshot,
    ExecutionClaim,
    RecoverableRun,
    claim_renewal_interval_seconds,
    complete_run,
    delete_runs,
    get_backlog_snapshot,
    get_group_run,
    get_group_runs,
    get_latest_finished_run,
    get_latest_run_for_fire_time,
    get_latest_runs,
    get_latest_targeted_run,
    get_recoverable_runs,
    get_runs,
    record_run_report,
    renew_claims,
    try_claim,
    try_queue_run,
)
from infrastructure.scheduling.scheduler.storage.task_store import (
    TaskStoreSnapshot,
    add_task,
    default_task_store_path,
    get_task,
    get_task_store_snapshot as _get_task_store_snapshot,
    list_tasks,
    record_task_success,
    remove_task,
    update_task,
)


def get_task_store_snapshot(
    store_path: Path | None = None, *, lock_timeout_seconds: float | None = None
) -> TaskStoreSnapshot:
    """Return task definitions, failing closed when a vanished store strands work.

    File absence is normal before scheduling is ever used. If durable waiting
    work exists, however, the missing definitions make eligibility and recovery
    unknowable, so expose the snapshot as incomplete instead of healthy-empty.
    """
    snapshot = _get_task_store_snapshot(
        store_path, lock_timeout_seconds=lock_timeout_seconds
    )
    if not snapshot.complete or not snapshot.missing:
        return snapshot

    db_path = run_database_path(store_path.parent) if store_path is not None else None
    try:
        backlog = get_backlog_snapshot(db_path=db_path)
    except (OSError, sqlite3.Error):
        return replace(snapshot, complete=False)
    if backlog.pending_count:
        return replace(snapshot, complete=False)
    return snapshot


__all__ = [
    "add_task",
    "BacklogSnapshot",
    "claim_renewal_interval_seconds",
    "complete_run",
    "default_run_database_path",
    "default_task_store_path",
    "delete_runs",
    "get_backlog_snapshot",
    "get_group_run",
    "get_group_runs",
    "ExecutionClaim",
    "RecoverableRun",
    "get_recoverable_runs",
    "get_latest_finished_run",
    "get_latest_run_for_fire_time",
    "get_latest_targeted_run",
    "get_latest_runs",
    "get_runs",
    "get_task",
    "get_task_store_snapshot",
    "list_tasks",
    "record_task_success",
    "record_run_report",
    "remove_task",
    "renew_claims",
    "run_database_path",
    "try_claim",
    "try_queue_run",
    "TaskStoreSnapshot",
    "update_task",
]