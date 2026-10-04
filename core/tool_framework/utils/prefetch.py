"""Start a tool's slow read before the model calls the tool, and hand the result to that call."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Hashable

logger = logging.getLogger(__name__)

_POLL_SECONDS = 0.05


class _Entry[V]:
    """One background read: set once by its thread, then read by the claiming call."""

    __slots__ = ("cancel", "done", "failed", "finished_at", "value")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.cancel = threading.Event()
        self.value: V | None = None
        self.failed = False
        self.finished_at = 0.0


class PrefetchRegistry[K: Hashable, V]:
    """Background reads keyed by the exact arguments of the tool call they stand in for.

    Each read runs once on a daemon thread and serves at most one call: ``claim``
    removes it, so a later call with the same arguments reads live again. A read
    that failed, or finished more than ``max_age_seconds`` ago, serves nobody.
    At most ``max_entries`` reads are kept, so threads and memory stay bounded.
    Thread-safe.
    """

    def __init__(
        self,
        *,
        name: str,
        max_age_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._max_age = max_age_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[K, _Entry[V]] = {}
        self._running: set[K] = set()

    def start(self, key: K, work: Callable[[Callable[[], bool]], V]) -> bool:
        """Run ``work(should_stop)`` in the background for ``key``; False when one is kept.

        Also False while a read for ``key`` still runs (claimed or not), and
        when ``max_entries`` reads are kept or running. ``work`` must not print
        or record anything: its result is only kept. ``should_stop`` turns true
        once a waiting call was cancelled, so the read can stop early.
        """
        entry: _Entry[V] = _Entry()
        with self._lock:
            self._drop_expired()
            if key in self._entries or key in self._running:
                return False
            if len(self._running) >= self._max_entries:
                return False
            if len(self._entries) >= self._max_entries and not self._evict_oldest_finished():
                return False
            self._entries[key] = entry
            self._running.add(key)
        thread = threading.Thread(
            target=self._run, args=(key, entry, work), name=f"{self._name}-prefetch", daemon=True
        )
        try:
            thread.start()
        except RuntimeError:
            logger.debug("Could not start the %s prefetch", self._name, exc_info=True)
            with self._lock:
                if self._entries.get(key) is entry:
                    del self._entries[key]
                self._running.discard(key)
            return False
        return True

    def claim(self, key: K, *, should_stop: Callable[[], bool] | None = None) -> V | None:
        """Take the read for ``key``, waiting while it runs; None when the call must read live.

        None when there is no read for ``key``, it failed, it is stale, or
        ``should_stop`` turned true while waiting (the caller's own read then
        sees the same stop); a stop also tells a running read to stop. The read
        is removed either way.
        """
        with self._lock:
            entry = self._entries.pop(key, None)
        if entry is None:
            return None
        while not entry.done.wait(_POLL_SECONDS):
            if should_stop is not None and should_stop():
                entry.cancel.set()
                return None
        if should_stop is not None and should_stop():
            return None
        if entry.failed or self._clock() - entry.finished_at > self._max_age:
            return None
        return entry.value

    def reset(self) -> None:
        """Forget every read; threads still running finish into entries nobody holds."""
        with self._lock:
            for entry in self._entries.values():
                entry.cancel.set()
            self._entries.clear()

    def _run(self, key: K, entry: _Entry[V], work: Callable[[Callable[[], bool]], V]) -> None:
        try:
            entry.value = work(entry.cancel.is_set)
        except Exception:
            # The tool call reads live and reports its own failure.
            logger.debug("The %s prefetch failed", self._name, exc_info=True)
            entry.failed = True
        finally:
            entry.finished_at = self._clock()
            entry.done.set()
            with self._lock:
                self._running.discard(key)

    def _drop_expired(self) -> None:
        now = self._clock()
        for key in [
            key
            for key, entry in self._entries.items()
            if entry.done.is_set() and (entry.failed or now - entry.finished_at > self._max_age)
        ]:
            del self._entries[key]

    def _evict_oldest_finished(self) -> bool:
        oldest = next((key for key, entry in self._entries.items() if entry.done.is_set()), None)
        if oldest is None:
            return False
        del self._entries[oldest]
        return True


__all__ = ["PrefetchRegistry"]
