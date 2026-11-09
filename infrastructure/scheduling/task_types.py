"""Task value types shared across opensre.

``TaskStatus`` / ``TaskKind`` / ``TaskRecord`` describe a single in-flight task
(a subprocess-backed CLI run or a code-agent run).

The persistent registry that stores and rehydrates these records lives in
:mod:`infrastructure.scheduling.task_registry`.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from subprocess import Popen
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskKind(StrEnum):
    CLI_COMMAND = "cli_command"
    CODE_AGENT = "code_agent"


@dataclass
class TaskRecord:
    """One shell task."""

    task_id: str
    kind: TaskKind
    status: TaskStatus = TaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    result: str | None = None
    error: str | None = None
    pid: int | None = None
    command: str | None = None
    progress: str | None = None

    _cancel_requested: threading.Event = field(
        default_factory=threading.Event, repr=False, init=False
    )
    _process: Popen[Any] | None = field(default=None, repr=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, init=False)
    _on_change: Callable[[], None] | None = field(default=None, repr=False, init=False)
    _rehydrated: bool = field(default=False, repr=False, init=False)

    def _notify_changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    @property
    def cancel_requested(self) -> threading.Event:
        """Set by :meth:`request_cancel`; polled by cooperative cancellation paths."""
        return self._cancel_requested

    def attach_process(self, proc: Popen[Any]) -> None:
        """Bind a child process so :meth:`request_cancel` can terminate it."""
        with self._lock:
            self._process = proc
            pid = getattr(proc, "pid", None)
            self.pid = pid if isinstance(pid, int) else None
        self._notify_changed()

    def attach_pid(self, pid: int | None) -> None:
        """Bind a previously-started process id without a live ``Popen`` object."""
        with self._lock:
            self.pid = pid
        self._notify_changed()

    def mark_running(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.RUNNING
        self._notify_changed()

    def mark_completed(self, *, result: str | None = None) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.COMPLETED
            self.result = result
            self.ended_at = time.time()
        self._notify_changed()

    def mark_cancelled(self) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.CANCELLED
            self.ended_at = time.time()
        self._notify_changed()

    def mark_failed(self, message: str) -> None:
        with self._lock:
            if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                return
            self.status = TaskStatus.FAILED
            self.error = message
            self.ended_at = time.time()
        self._notify_changed()

    def request_cancel(self) -> bool:
        """Signal cancellation and kill a bound subprocess. Returns True if task was running."""
        mark_cancelled_without_watcher = False
        with self._lock:
            was_active = self.status == TaskStatus.RUNNING
            self._cancel_requested.set()
            proc = self._process
            pid = self.pid
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
        elif was_active and pid is not None:
            mark_cancelled_without_watcher = True
        if mark_cancelled_without_watcher:
            self.mark_cancelled()
        else:
            self._notify_changed()
        return was_active

    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def refresh_rehydrated_status(self) -> None:
        """Mark persisted running tasks as finished once their PID disappears."""
        with self._lock:
            if (
                not self._rehydrated
                or self.status != TaskStatus.RUNNING
                or self._process is not None
            ):
                return
            if self.pid is not None and _process_alive(self.pid):
                return
            self.status = TaskStatus.COMPLETED
            self.result = self.result or "process exited while shell was closed"
            self.ended_at = time.time()
        self._notify_changed()

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
            "pid": self.pid,
            "command": self.command,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> TaskRecord | None:
        try:
            task_id = str(data["task_id"])
            kind = TaskKind(str(data["kind"]))
            status = TaskStatus(str(data["status"]))
            started_at_value = data["started_at"]
            if not isinstance(started_at_value, int | float | str):
                return None
            started_at = float(started_at_value)
        except (KeyError, TypeError, ValueError):
            return None

        ended_at_value = data.get("ended_at")
        pid_value = data.get("pid")
        record = cls(
            task_id=task_id,
            kind=kind,
            status=status,
            started_at=started_at,
            ended_at=float(ended_at_value) if isinstance(ended_at_value, int | float) else None,
            result=str(data["result"]) if data.get("result") is not None else None,
            error=str(data["error"]) if data.get("error") is not None else None,
            progress=str(data["progress"]) if data.get("progress") is not None else None,
            pid=int(pid_value) if isinstance(pid_value, int) else None,
            command=str(data["command"]) if data.get("command") is not None else None,
        )
        record._rehydrated = True
        return record

    def update_progress(self, output: str) -> None:
        line = output.rstrip("\r\n")
        if not line:
            return
        with self._lock:
            self.progress = line


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = ["TaskKind", "TaskRecord", "TaskStatus"]
