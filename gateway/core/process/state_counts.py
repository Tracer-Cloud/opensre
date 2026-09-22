"""How much persisted state this gateway serves: sessions, memory notes, scheduled tasks."""

from __future__ import annotations

from dataclasses import dataclass

from filelock import Timeout

from config.constants.gateway import HEALTH_TASK_STORE_LOCK_TIMEOUT_SECONDS
from config.constants.paths import get_memory_dir, get_sessions_dir
from infrastructure.scheduling.scheduler.storage import (
    default_task_store_path,
    get_task_store_snapshot,
)


@dataclass(frozen=True)
class StateCounts:
    """Counts a caller can compare across a stop and a start."""

    sessions: int
    memory_notes: int
    scheduled_tasks: int


def read_state_counts() -> StateCounts:
    """Count what is on the state volume right now; unreadable stores count as zero."""
    sessions = _count_session_files()
    memory_notes = _count_memory_notes()
    scheduled_tasks = _count_scheduled_tasks()
    return StateCounts(
        sessions=sessions,
        memory_notes=memory_notes,
        scheduled_tasks=scheduled_tasks,
    )


def _count_session_files() -> int:
    sessions_dir = get_sessions_dir()
    if not sessions_dir.exists():
        return 0
    files = list(sessions_dir.glob("*.jsonl"))
    return len(files)


def _count_memory_notes() -> int:
    memory_dir = get_memory_dir()
    if not memory_dir.exists():
        return 0
    notes = list(memory_dir.glob("*.md"))
    return len(notes)


def _count_scheduled_tasks() -> int:
    """Bounded wait for the task-store lock; a held lock counts as zero rather than blocking."""
    store_path = default_task_store_path()
    try:
        snapshot = get_task_store_snapshot(
            store_path, lock_timeout_seconds=HEALTH_TASK_STORE_LOCK_TIMEOUT_SECONDS
        )
    except Timeout:
        return 0
    return len(snapshot.tasks)


__all__ = ["StateCounts", "read_state_counts"]
