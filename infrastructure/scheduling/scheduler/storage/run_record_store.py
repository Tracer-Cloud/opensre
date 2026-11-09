"""File-backed run records: ``scheduler_runs/<task id>.json`` beside the task store.

One JSON file per task holds its newest attempts, newest first. Writes take a
per-file lock and replace the file through a fsynced temp file, so a reader
(the shell, or the S3 export of the OpenSRE home) never sees half a file. The
run database stays the authority for claims; these files are its inspectable
copy, so an unreadable file is replaced rather than repaired.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

from infrastructure.scheduling.scheduler.storage import task_store

logger = logging.getLogger(__name__)

RUN_RECORDS_DIRNAME = "scheduler_runs"
RUN_RECORDS_VERSION = 1
RUNS_PER_TASK = 20
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def run_records_path(task_id: str) -> Path:
    """The record file for ``task_id``; an ID unsafe as a file name is stored under its hash."""
    name = (
        task_id
        if _TASK_ID.match(task_id)
        else f"sha256-{hashlib.sha256(task_id.encode()).hexdigest()}"
    )
    return task_store.default_task_store_path().parent / RUN_RECORDS_DIRNAME / f"{name}.json"


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Replacing unreadable run records %s: %s", path, exc)
        return []
    runs = data.get("runs") if isinstance(data, dict) else None
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


def _attempt_key(record: dict[str, Any]) -> tuple[str, int]:
    attempt = record.get("attempt")
    return str(record.get("fire_time", "")), attempt if isinstance(attempt, int) else 0


def save_run_record(record: dict[str, Any]) -> None:
    """Insert or replace one attempt's record, keeping the task's newest ``RUNS_PER_TASK``."""
    task_id = str(record.get("task_id", ""))
    path = run_records_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = _attempt_key(record)
    with FileLock(path.with_suffix(".lock")):
        runs = [run for run in _read(path) if _attempt_key(run) != key]
        runs.append(record)
        runs.sort(
            key=lambda run: (str(run.get("started_at", "")), *_attempt_key(run)), reverse=True
        )
        payload = {"version": RUN_RECORDS_VERSION, "task_id": task_id, "runs": runs[:RUNS_PER_TASK]}
        _write_atomic(path, payload)


def read_run_records(task_id: str) -> list[dict[str, Any]]:
    """Saved records for ``task_id``, newest first."""
    return _read(run_records_path(task_id))


__all__ = [
    "RUNS_PER_TASK",
    "RUN_RECORDS_DIRNAME",
    "read_run_records",
    "run_records_path",
    "save_run_record",
]
