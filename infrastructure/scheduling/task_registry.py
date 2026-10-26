"""Persistent registry for in-flight interactive-shell tasks (/tasks, /cancel).

The task value types (:class:`TaskStatus`, :class:`TaskKind`,
:class:`TaskRecord`) live in :mod:`infrastructure.scheduling.task_types` so non-CLI
packages can share the task contract without importing the CLI package. This
module owns only the CLI-runtime registry that stores and persists those records
across REPL sessions.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import threading
from collections import deque
from pathlib import Path

import config.constants as const_module
from infrastructure.scheduling.task_types import TaskKind, TaskRecord, TaskStatus

_TASK_ID_BYTES = 4
_MAX_REGISTRY = 100
_TASKS_STORE_FILENAME = "interactive_tasks.json"


def _tasks_store_path() -> Path:
    return const_module.OPENSRE_HOME_DIR / _TASKS_STORE_FILENAME


class TaskRegistry:
    """Recent tasks for /tasks and /cancel, optionally persisted across REPL sessions."""

    def __init__(
        self,
        *,
        max_tasks: int = _MAX_REGISTRY,
        persist_path: Path | None = None,
        load: bool = False,
    ) -> None:
        self._tasks: deque[TaskRecord] = deque(maxlen=max_tasks)
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._persist_path = persist_path
        self._max_tasks = max_tasks
        if load:
            self._load_persisted()

    @classmethod
    def persistent(cls, *, max_tasks: int = _MAX_REGISTRY) -> TaskRegistry:
        return cls(max_tasks=max_tasks, persist_path=_tasks_store_path(), load=True)

    def _attach(self, record: TaskRecord) -> TaskRecord:
        record._on_change = self._persist
        return record

    def _load_persisted(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, list):
            return
        records = [
            self._attach(record)
            for item in payload
            if isinstance(item, dict)
            if (record := TaskRecord.from_dict(item)) is not None
        ]
        for record in records[-self._max_tasks :]:
            record.refresh_rehydrated_status()
            self._tasks.append(record)
        self._persist()

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        with self._persist_lock:
            with self._lock:
                payload = [task.to_dict() for task in self._tasks]
            tmp_path: Path | None = None
            try:
                self._persist_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                tmp_path = self._persist_path.with_name(
                    f"{self._persist_path.name}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
                )
                tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                tmp_path.replace(self._persist_path)
            except OSError:
                if tmp_path is not None:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink()
                return

    def _refresh_rehydrated(self) -> None:
        with self._lock:
            items = list(self._tasks)
        for task in items:
            task.refresh_rehydrated_status()

    def _tasks_from_disk(self) -> list[TaskRecord]:
        """Read the persisted store and return records not already in memory.

        Called by :meth:`list_recent` so that tasks created by other REPL
        sessions (which share the same on-disk store) are visible without
        requiring a full restart.
        """
        if self._persist_path is None or not self._persist_path.exists():
            return []
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        with self._lock:
            known_ids = {task.task_id for task in self._tasks}
        records: list[TaskRecord] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            record = TaskRecord.from_dict(item)
            if record is None or record.task_id in known_ids:
                continue
            record._rehydrated = True
            record.refresh_rehydrated_status()
            records.append(record)
        return records

    def create(self, kind: TaskKind, *, command: str | None = None) -> TaskRecord:
        task_id = secrets.token_hex(_TASK_ID_BYTES)
        record = self._attach(TaskRecord(task_id=task_id, kind=kind, command=command))
        with self._lock:
            self._tasks.append(record)
        self._persist()
        return record

    def candidates(self, task_id_prefix: str) -> list[TaskRecord]:
        self._refresh_rehydrated()
        needle = task_id_prefix.strip().lower()
        if not needle:
            return []
        with self._lock:
            items = list(self._tasks)
        return [t for t in items if t.task_id.lower().startswith(needle)]

    def get(self, task_id_prefix: str) -> TaskRecord | None:
        matches = self.candidates(task_id_prefix)
        if len(matches) != 1:
            return None
        return matches[0]

    def running_count(self) -> int:
        """Count in-memory running tasks (no disk merge — safe for hot prompt refresh)."""
        with self._lock:
            return sum(1 for task in self._tasks if task.status == TaskStatus.RUNNING)

    def list_recent(self, n: int = 20) -> list[TaskRecord]:
        """Return up to ``n`` tasks, newer tasks first.

        Merges any tasks written to the on-disk store by other REPL sessions
        (e.g. a parallel terminal) so the view is always up-to-date across
        concurrent sessions that share the same persistence file.
        """
        self._refresh_rehydrated()
        disk_extras = self._tasks_from_disk()
        with self._lock:
            items = list(self._tasks)
        combined = items + disk_extras
        combined.sort(key=lambda t: t.started_at)
        return list(reversed(combined[-n:]))

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
        self._persist()

    def __contains__(self, task_id: str) -> bool:
        return self.get(task_id) is not None


__all__ = ["TaskRegistry"]
