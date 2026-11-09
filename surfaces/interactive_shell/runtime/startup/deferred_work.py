"""Hold non-essential launch work until the shell first waits on the user.

Until the first menu or prompt is on screen, every extra Python thread competes
with the launch for the interpreter lock: an analytics snapshot that imports the
whole tool registry, or a model-client warm-up that imports its SDK, delays the
first paint by seconds. Work that only needs to be done "soon" is deferred here
and released the moment the shell starts waiting on a person.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

_THREAD_NAME = "opensre-deferred-startup"
# ``release`` is called just before the first menu draws; the held work waits
# this long so that draw, not the work's imports, gets the interpreter first.
_SETTLE_SECONDS = 0.2

DeferredJob = Callable[[], object]


class DeferredStartupWork:
    """Launch work held back until :meth:`release`; thread-safe and idempotent.

    Released work runs in deferral order on one daemon thread, so the jobs do
    not also compete with each other. Work deferred after release starts at
    once; work still held at :meth:`close` is dropped. A job's failure is
    logged and never reaches the shell.
    """

    def __init__(self, *, settle_seconds: float = _SETTLE_SECONDS) -> None:
        self._lock = threading.Lock()
        self._held: list[tuple[str, DeferredJob]] = []
        self._released = False
        self._closed = False
        self._settle_seconds = settle_seconds

    def defer(self, name: str, job: DeferredJob) -> None:
        """Hold ``job`` until release, or run it now when already released."""
        with self._lock:
            if self._closed:
                return
            if not self._released:
                self._held.append((name, job))
                return
        self._start([(name, job)], settle_seconds=0.0)

    def release(self) -> None:
        """Start the held work; later calls do nothing."""
        with self._lock:
            if self._released or self._closed:
                return
            self._released = True
            held, self._held = self._held, []
        if held:
            self._start(held, settle_seconds=self._settle_seconds)

    def close(self) -> None:
        """Drop work that was never released (the shell is exiting)."""
        with self._lock:
            self._closed = True
            self._held.clear()

    def _start(self, jobs: list[tuple[str, DeferredJob]], *, settle_seconds: float) -> None:
        threading.Thread(
            target=self._run,
            args=(jobs, settle_seconds),
            name=_THREAD_NAME,
            daemon=True,
        ).start()

    def _run(self, jobs: list[tuple[str, DeferredJob]], settle_seconds: float) -> None:
        if settle_seconds > 0:
            time.sleep(settle_seconds)
        for name, job in jobs:
            with self._lock:
                if self._closed:
                    return
            try:
                job()
            except Exception:
                logger.debug("Deferred startup job %r failed", name, exc_info=True)


__all__ = ["DeferredJob", "DeferredStartupWork"]
