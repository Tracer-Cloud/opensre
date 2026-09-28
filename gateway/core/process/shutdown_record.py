"""Whether the previous gateway process stopped cleanly, kept on the state volume.

The running process writes ``running`` at start and ``stopped`` at the end of
``stop``. A process that is killed leaves ``running`` behind, so the next start
can tell a clean stop from an interrupted one.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from config.constants.paths import host_home

#: Component name under which the controller publishes how the previous process ended.
PREVIOUS_SHUTDOWN_COMPONENT = "previous shutdown"

STATE_RUNNING = "running"
STATE_STOPPED = "stopped"

FIRST_START = "first start"
CLEAN = "clean"
UNCLEAN_KILLED = "unclean: the process ended without stopping"
UNCLEAN_CHAT_WORKERS = "unclean: chat workers were still running"
UNCLEAN_SCHEDULED_JOBS = "unclean: scheduled jobs were still running"
UNCLEAN_UNREADABLE = "unclean: the shutdown record could not be read"


@dataclass(frozen=True)
class ShutdownRecord:
    """What one gateway process last wrote about itself."""

    state: str
    recorded_at: str
    chat_workers_stopped: bool = True
    scheduled_jobs_finished: bool = True

    @property
    def clean(self) -> bool:
        stopped = self.state == STATE_STOPPED
        return stopped and self.chat_workers_stopped and self.scheduled_jobs_finished


def shutdown_record_path() -> Path:
    """Resolved on every call so a relocated home is honored."""
    return host_home() / "gateway" / "shutdown.json"


def record_running() -> None:
    """Mark this process as running; a kill leaves this behind."""
    record = ShutdownRecord(state=STATE_RUNNING, recorded_at=_now())
    _write(record)


def record_stopped(*, chat_workers_stopped: bool, scheduled_jobs_finished: bool) -> None:
    """Mark this process as stopped, with what did not finish in time."""
    record = ShutdownRecord(
        state=STATE_STOPPED,
        recorded_at=_now(),
        chat_workers_stopped=chat_workers_stopped,
        scheduled_jobs_finished=scheduled_jobs_finished,
    )
    _write(record)


def describe_previous_shutdown() -> str:
    """Say how the previous process ended, for the status listing and the log."""
    path = shutdown_record_path()
    if not path.exists():
        return FIRST_START
    record = _read(path)
    if record is None:
        return UNCLEAN_UNREADABLE
    if record.state != STATE_STOPPED:
        return UNCLEAN_KILLED
    if not record.chat_workers_stopped:
        return UNCLEAN_CHAT_WORKERS
    if not record.scheduled_jobs_finished:
        return UNCLEAN_SCHEDULED_JOBS
    return CLEAN


def _read(path: Path) -> ShutdownRecord | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ShutdownRecord(
            state=str(payload["state"]),
            recorded_at=str(payload["recorded_at"]),
            chat_workers_stopped=bool(payload["chat_workers_stopped"]),
            scheduled_jobs_finished=bool(payload["scheduled_jobs_finished"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(record: ShutdownRecord) -> None:
    """Replace the record atomically so a kill mid-write cannot leave half a file."""
    path = shutdown_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(asdict(record), indent=2)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".shutdown-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_name, path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "CLEAN",
    "FIRST_START",
    "PREVIOUS_SHUTDOWN_COMPONENT",
    "UNCLEAN_CHAT_WORKERS",
    "UNCLEAN_KILLED",
    "UNCLEAN_SCHEDULED_JOBS",
    "UNCLEAN_UNREADABLE",
    "ShutdownRecord",
    "describe_previous_shutdown",
    "record_running",
    "record_stopped",
    "shutdown_record_path",
]
