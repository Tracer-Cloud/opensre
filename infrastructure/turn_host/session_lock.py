"""Cross-process execution lock shared by every local session host."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from core.agent_harness.spi.defaults import sessions_dir


class SessionExecutionBusyError(RuntimeError):
    """Raised when another host already owns a session's execution lease."""


@contextmanager
def session_execution_lock(session_id: str, *, timeout: float = -1) -> Iterator[None]:
    """Hold the shared whole-turn lease for ``session_id``."""
    lock_dir = sessions_dir() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    try:
        with FileLock(lock_dir / f"{digest}.lock", timeout=timeout):
            yield
    except Timeout as exc:
        raise SessionExecutionBusyError(session_id) from exc


__all__ = ["SessionExecutionBusyError", "session_execution_lock"]
