"""Cross-process execution lock shared by every local session host."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from filelock import FileLock, Timeout

from core.agent_harness.spi.defaults import sessions_dir


class SessionExecutionBusyError(RuntimeError):
    """Raised when another host already owns a session's execution lease."""


_thread_leases = threading.local()
_thread_retained_lease_scopes = threading.local()


def _leases_held_by_current_thread() -> dict[str, tuple[FileLock, int]]:
    """Return this thread's reentrant leases and their nesting depths."""
    leases = getattr(_thread_leases, "leases", None)
    if leases is None:
        leases = {}
        _thread_leases.leases = leases
    return leases


def _retained_lease_scopes_for_current_thread() -> list[list[AbstractContextManager[None]]]:
    """Return the active whole-turn lease scopes for this thread."""
    scopes = getattr(_thread_retained_lease_scopes, "scopes", None)
    if scopes is None:
        scopes = []
        _thread_retained_lease_scopes.scopes = scopes
    return scopes


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


@contextmanager
def retained_session_execution_locks() -> Iterator[None]:
    """Keep any lease retained during this scope until the enclosing turn ends.

    A slash command can move a live shell from session A to session B during a
    turn that already owns A.  It must take B without waiting (to avoid the
    A -> B / B -> A deadlock), then keep B protected through that turn's final
    persistence.  This scope gives that deliberately narrow hand-off a
    deterministic release point without putting transient locks on SessionCore.
    """
    scopes = _retained_lease_scopes_for_current_thread()
    retained: list[AbstractContextManager[None]] = []
    scopes.append(retained)
    try:
        yield
    finally:
        scopes.pop()
        while retained:
            # Context managers returned by ``session_execution_lock`` do not
            # suppress exceptions, and releasing in reverse matches ``with``.
            retained.pop().__exit__(None, None, None)


def retain_session_execution_lock(
    session_id: str,
    *,
    timeout: float = -1,
    reentrant: bool = False,
) -> bool:
    """Retain a session lease for the active whole-turn scope when present.

    ``False`` means the caller is outside a turn host, where it should use the
    ordinary lexical :func:`session_execution_lock` instead.  Busy leases still
    raise :class:`SessionExecutionBusyError`, just like the lexical form.
    """
    scopes = _retained_lease_scopes_for_current_thread()
    if not scopes:
        return False
    lease = session_execution_lock(session_id, timeout=timeout, reentrant=reentrant)
    lease.__enter__()
    scopes[-1].append(lease)
    return True


__all__ = [
    "SessionExecutionBusyError",
    "retain_session_execution_lock",
    "retained_session_execution_locks",
    "session_execution_lock",
]
