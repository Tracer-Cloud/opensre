"""Cross-store backlog status orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import infrastructure.scheduling.scheduler.storage.database as database
from infrastructure.scheduling.scheduler.storage.task_store import TaskStoreSnapshot
from infrastructure.scheduling.scheduler.storage.task_store import (
    get_task_store_snapshot as _get_task_store_snapshot,
)


class BacklogStatusRunStoreError(RuntimeError):
    """Run storage could not be inspected while validating a missing task store."""


def _run_store_has_history(db_path: Path) -> bool:
    """Return whether an existing run database proves scheduler state existed."""
    try:
        db_path.stat()
    except FileNotFoundError:
        return False

    with database.connection(db_path) as conn:
        row = conn.execute("SELECT EXISTS(SELECT 1 FROM task_runs LIMIT 1)").fetchone()
    return bool(row and row[0])


def get_task_store_snapshot(
    store_path: Path | None = None, *, lock_timeout_seconds: float | None = None
) -> TaskStoreSnapshot:
    """Return task definitions, failing closed when a vanished store hides run history."""
    snapshot = _get_task_store_snapshot(store_path, lock_timeout_seconds=lock_timeout_seconds)
    if not snapshot.complete or not snapshot.missing:
        return snapshot

    db_path = (
        database.run_database_path(store_path.parent)
        if store_path is not None
        else database.default_run_database_path()
    )
    try:
        has_history = _run_store_has_history(db_path)
    except (OSError, sqlite3.Error) as exc:
        raise BacklogStatusRunStoreError(
            "could not inspect scheduler run storage while validating a missing task store"
        ) from exc
    if has_history:
        return replace(snapshot, complete=False)
    return snapshot


__all__ = ["BacklogStatusRunStoreError", "get_task_store_snapshot"]
