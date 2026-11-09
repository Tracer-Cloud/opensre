"""Cross-process signal for live scheduler hosts to resync jobs from the store.

The interactive shell and CLI mutate the task store; the long-lived gateway
scheduler only sees those mutations when it reloads. Writers touch a file
under ``~/.opensre``; the gateway polls and resyncs. Do not add a second
scheduler inside ``gateway/``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR

logger = logging.getLogger(__name__)

_RELOAD_FILENAME = "scheduler_reload_requested"
# Gateway poll interval for the reload signal file.
RELOAD_POLL_SECONDS = 2.0


def _signal_path() -> Path:
    return OPENSRE_HOME_DIR / _RELOAD_FILENAME


def request_scheduler_reload() -> None:
    """Ask any live scheduler host to resync jobs from the task store.

    Best-effort: a failure to write the signal is logged, never raised, so it
    cannot fail the store mutation that triggered it — the scheduler still
    resyncs on its next poll or on restart.
    """
    path = _signal_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError:
        logger.warning("Could not write scheduler reload signal at %s", path, exc_info=True)


def consume_scheduler_reload_request() -> bool:
    """Return True once if a reload was requested, clearing the signal.

    Atomic: the unlink both tests for and clears the request, so two polls
    cannot both observe the same one.
    """
    try:
        _signal_path().unlink()
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not clear scheduler reload signal", exc_info=True)
        return False
    return True


def _store_signature(store_path: Path) -> tuple[int, int] | None:
    """The task store's (mtime_ns, size), or None if it does not exist yet."""
    try:
        stat = store_path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def watch_and_reconcile(
    stop_event: threading.Event,
    reconcile: Callable[[], object],
    store_path: Path,
    *,
    poll_seconds: float = RELOAD_POLL_SECONDS,
    on_error: Callable[[BaseException], None] | None = None,
) -> None:
    """Keep a live scheduler in sync with the task store until ``stop_event``.

    Reconciles when a reload signal arrives (fast path) or when the store file
    itself changed since the last reconcile. The file's ``(mtime, size)`` is the
    authoritative level-state, so a dropped best-effort signal still converges on
    the next poll, and an idle store is a cheap ``stat`` with no work. On a
    reconcile failure the signature is not advanced, so the next poll retries.
    """
    last = _store_signature(store_path)
    while not stop_event.wait(poll_seconds):
        signalled = consume_scheduler_reload_request()
        signature = _store_signature(store_path)
        if not signalled and signature == last:
            continue
        try:
            reconcile()
            last = signature
        except Exception as exc:  # noqa: BLE001
            if on_error is not None:
                on_error(exc)


__all__ = [
    "RELOAD_POLL_SECONDS",
    "consume_scheduler_reload_request",
    "request_scheduler_reload",
    "watch_and_reconcile",
]
