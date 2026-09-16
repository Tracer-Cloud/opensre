"""Cross-store backlog status orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from infrastructure.scheduling.scheduler.storage.database import run_database_path
from infrastructure.scheduling.scheduler.storage.run_store import get_backlog_snapshot
from infrastructure.scheduling.scheduler.storage.task_store import TaskStoreSnapshot
from infrastructure.scheduling.scheduler.storage.task_store import (
    get_task_store_snapshot as _get_task_store_snapshot,
)


def get_task_store_snapshot(
    store_path: Path | None = None, *, lock_timeout_seconds: float | None = None
) -> TaskStoreSnapshot:
    """Return task definitions, failing closed when a vanished store strands work."""
    snapshot = _get_task_store_snapshot(store_path, lock_timeout_seconds=lock_timeout_seconds)
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


__all__ = ["get_task_store_snapshot"]
