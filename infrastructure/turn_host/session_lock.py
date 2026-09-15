"""Cross-process execution lock shared by every local session host."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from filelock import FileLock, Timeout

from core.agent_harness.spi.defaults import sessions_dir


class SessionExecutionBusyError(RuntimeError):
    """Raised when another host already owns a session's execution lease."""


_thread_leases = threading.local()


def _leases_held_by_current_thread() -> dict[str, tuple[FileLock, int]]:
    """Return this thread's reentrant leases and their nesting depths."""
    leases = getattr(_thread_leases, "leases", None)
    if leases is None:
        leases = {}
        _thread_leases.leases = leases
    return leases


@contextmanager
def session_execution_lock(
    session_id: str,
    *,
    timeout: float = -1,
    reentrant: bool = False,
) -> Iterator[None]:
    """Hold the shared whole-turn lease for ``session_id``.

    ``reentrant=True`` lets a deliberately nested caller in one thread share
    the physical lease.  The gateway uses that only when its already-leased
    turn enters the session-agent pool; other callers continue to detect an
    overlapping lease as busy.
    """
    held_leases = _leases_held_by_current_thread()
    existing_lease = held_leases.get(session_id)
    if existing_lease is not None and reentrant:
        lock, depth = existing_lease
        held_leases[session_id] = (lock, depth + 1)
        try:
            yield
        finally:
            _, remaining_depth = held_leases[session_id]
            held_leases[session_id] = (lock, remaining_depth - 1)
        return

    lock_dir = sessions_dir() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    lock = FileLock(lock_dir / f"{digest}.lock")
    try:
        lock.acquire(timeout=timeout)
    except Timeout as exc:
        raise SessionExecutionBusyError(session_id) from exc
    held_leases[session_id] = (lock, 1)
    try:
        yield
    finally:
        held_leases.pop(session_id)
        lock.release()


__all__ = ["SessionExecutionBusyError", "session_execution_lock"]
