"""JSON-backed task definition CRUD with file locking."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from config.constants import OPENSRE_HOME_DIR
from infrastructure.scheduling.scheduler import reload_signal
from infrastructure.scheduling.scheduler.storage.database import run_database_path
from infrastructure.scheduling.scheduler.storage.run_store import delete_runs
from infrastructure.scheduling.scheduler.types import ScheduledTask

logger = logging.getLogger(__name__)

_STORE_FILENAME = "scheduler_tasks.json"


def default_task_store_path() -> Path:
    """Return the scheduler task-store path under the OpenSRE home."""
    return OPENSRE_HOME_DIR / _STORE_FILENAME


def _lock_path(store_path: Path) -> Path:
    return store_path.with_suffix(".lock")


def _read_raw(store_path: Path) -> tuple[list[dict[str, object]], bool]:
    """Load the raw task list; the flag reports whether the file was readable.

    A missing store is readable and empty. A store that will not parse is
    ``([], False)`` -- callers about to write must not treat that as "no
    tasks" and silently overwrite it.
    """
    if not store_path.exists():
        return [], True
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read scheduler store: %s", exc)
        return [], False
    if not isinstance(data, list):
        logger.warning("Scheduler store is not a JSON list; treating it as unreadable")
        return [], False
    return data, True  # type: ignore[return-value]


def _load_raw(store_path: Path) -> list[dict[str, object]]:
    """Load the raw task list, treating an unreadable store as empty."""
    return _read_raw(store_path)[0]


def _fsync_parent_dir(path: Path) -> None:
    """Best-effort directory fsync so an ``os.replace`` survives a power loss on Unix.

    ``os.replace`` is atomic, but the directory entry change it makes is only
    guaranteed durable once the directory itself is fsynced -- without this, a
    crash immediately after replace can leave the directory pointing at the
    old inode again, even though the new file's own contents were fsynced.
    Windows has no equivalent operation, so this is a no-op there.
    """
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def _quarantine_unreadable(store_path: Path) -> None:
    """Move an unparseable store aside so a write cannot destroy it.

    The name carries a timestamp for the operator and a unique suffix for
    correctness: ``mkstemp`` reserves the destination atomically, so two
    corruptions inside the same second cannot land on one name and have the
    second ``os.replace`` erase the first casualty.
    """
    fd, aside_str = tempfile.mkstemp(
        dir=store_path.parent,
        prefix=f"{store_path.name}.corrupt-{int(time.time())}-",
    )
    os.close(fd)
    aside = Path(aside_str)
    try:
        os.replace(store_path, aside)
    except OSError:
        # mkstemp already created the (empty) aside file; a failed replace
        # would otherwise leave it behind as a misleading empty "recovery"
        # artifact that isn't actually the quarantined data.
        with contextlib.suppress(OSError):
            aside.unlink()
        raise
    _fsync_parent_dir(aside)
    logger.error(
        "Scheduler store at %s was unreadable and has been preserved at %s. "
        "Scheduled tasks in it are recoverable from that file.",
        store_path,
        aside,
    )


def _load_for_write(store_path: Path) -> list[dict[str, object]]:
    """Load the task list for a call about to write it back.

    An unreadable store is moved aside first, so the write lands on a fresh
    file and the damaged one stays on disk for recovery.
    """
    raw, readable = _read_raw(store_path)
    if not readable:
        _quarantine_unreadable(store_path)
    return raw


def _save_raw(store_path: Path, data: list[dict[str, object]]) -> None:
    """Persist the task list by atomic rename.

    ``Path.write_text`` truncates before rewriting, so a crash inside that
    window leaves a half-written file that no longer parses. Every other
    local store in the repo writes through a temp file in the destination
    directory, fsyncs, then ``os.replace``; see
    ``integrations/store.py::_atomic_write``. The directory fsync afterward
    (``_fsync_parent_dir``) mirrors
    ``infrastructure/analytics/provider.py::_write_text_atomic``, so the
    rename itself survives a power loss, not just the file's own contents.
    """
    store_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, default=str) + "\n"
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=store_path.parent, prefix=store_path.name + ".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, store_path)
        _fsync_parent_dir(store_path)
    except Exception:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise


def list_tasks(store_path: Path | None = None) -> list[ScheduledTask]:
    """Return all persisted scheduled tasks."""
    path = store_path or default_task_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
    tasks: list[ScheduledTask] = []
    for entry in raw:
        try:
            tasks.append(ScheduledTask.model_validate(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid task entry: %s", exc)
    return tasks


def get_task(task_id: str, store_path: Path | None = None) -> ScheduledTask | None:
    """Return a single task by ID, or None if not found."""
    for task in list_tasks(store_path):
        if task.id == task_id:
            return task
    return None


def _schedule_identity(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two rows the same schedule.

    Full configuration, not just the slot: two rows differing in destination or
    params are separate reports, and merging them would drop one the user asked
    for. Identity deliberately excludes ``id``, ``name``, skill revision, and the
    run bookkeeping (``created_at``, ``last_run``, ``next_run``), which differ
    between two confirmations of the same schedule.
    """
    return (
        entry.get("kind"),
        entry.get("cron"),
        entry.get("timezone"),
        entry.get("provider"),
        entry.get("chat_id"),
        entry.get("window_hours"),
        entry.get("skill_name") or "",
        tuple(sorted((entry.get("skill_inputs") or {}).items())),
        tuple(sorted((entry.get("params") or {}).items())),
    )


def add_task(task: ScheduledTask, store_path: Path | None = None) -> ScheduledTask:
    """Persist a scheduled task, or update the matching schedule's skill revision.

    Confirming the same schedule twice is one schedule. Without this, every
    confirmation appended a row — a real install reached 37 byte-identical
    ``daily_summary`` entries, none of which could deliver.
    """
    path = store_path or default_task_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_for_write(path)
        wanted = _schedule_identity(task.model_dump(mode="json"))
        existing_index = next(
            (index for index, entry in enumerate(raw) if _schedule_identity(entry) == wanted),
            None,
        )
        if existing_index is not None:
            existing = raw[existing_index]
            if existing.get("skill_revision", "") == task.skill_revision:
                return ScheduledTask.model_validate(existing)
            existing["skill_revision"] = task.skill_revision
            stored_task = ScheduledTask.model_validate(existing)
        else:
            raw.append(task.model_dump(mode="json"))
            stored_task = task
        _save_raw(path, raw)
    # A new task or pinned revision changed the schedule: wake the scheduler to resync.
    reload_signal.request_scheduler_reload()
    return stored_task


def remove_task(task_id: str, store_path: Path | None = None) -> bool:
    """Remove a task by ID and cascade-delete its run records.

    Returns True if the task was found and removed from the JSON store.
    Cascade deletion of ``TaskRun`` records in the SQLite claim store is
    best-effort — a warning is logged on failure but the return value
    reflects only the JSON-store result.
    """
    path = store_path or default_task_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
        original_len = len(raw)
        raw = [entry for entry in raw if entry.get("id") != task_id]
        if len(raw) == original_len:
            return False
        _save_raw(path, raw)

    # The schedule changed: wake any running scheduler so it stops firing this.
    reload_signal.request_scheduler_reload()

    # Cascade: remove orphaned TaskRun records from the SQLite claim store.
    # Derive the DB path from the same directory as the JSON store.
    db_path = run_database_path(path.parent)
    try:
        deleted = delete_runs(task_id, db_path)
        if deleted:
            logger.info("Cascade-deleted %d run(s) for removed task %s", deleted, task_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to cascade-delete runs for task %s (DB: %s); orphaned runs may remain",
            task_id,
            db_path,
            exc_info=True,
        )

    return True


def update_task(task: ScheduledTask, store_path: Path | None = None) -> bool:
    """Update an existing task in the store. Returns True if found and updated."""
    path = store_path or default_task_store_path()
    lock = FileLock(_lock_path(path))
    with lock:
        raw = _load_raw(path)
        for i, entry in enumerate(raw):
            if entry.get("id") == task.id:
                raw[i] = task.model_dump(mode="json")
                _save_raw(path, raw)
                return True
    return False


def record_task_success(task_id: str, store_path: Path | None = None) -> bool:
    """Update completion fields on the latest task while preserving user edits."""
    path = store_path or default_task_store_path()
    with FileLock(_lock_path(path)):
        raw = _load_raw(path)
        for entry in raw:
            if entry.get("id") == task_id:
                task = ScheduledTask.model_validate(entry)
                entry["last_run"] = datetime.now(UTC).isoformat()
                if task.params.get("disable_after_success", "").strip().lower() == "true":
                    entry["enabled"] = False
                _save_raw(path, raw)
                return True
    return False


__all__ = [
    "add_task",
    "default_task_store_path",
    "get_task",
    "list_tasks",
    "record_task_success",
    "remove_task",
    "update_task",
]
