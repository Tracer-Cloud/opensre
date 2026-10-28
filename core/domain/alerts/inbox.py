"""In-process alert inbox — the domain queue for external alert pushes.

Owns the queue and the process-wide current-inbox handle. HTTP intake is
``POST /alerts`` on the gateway web app.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime

from config.strict_config import StrictConfigModel

_DEFAULT_MAX_INBOX = 256

#: Seconds a requeued alert waits before it is eligible for another drain.
_DEFAULT_RETRY_AFTER = 5.0


class IncomingAlert(StrictConfigModel):
    text: str
    alert_name: str | None = None
    severity: str | None = None
    source: str | None = None
    received_at: datetime | None = None


class AlertInbox:
    def __init__(
        self,
        maxsize: int = _DEFAULT_MAX_INBOX,
        *,
        retry_after: float = _DEFAULT_RETRY_AFTER,
    ) -> None:
        self._queue: deque[IncomingAlert] = deque()
        #: Alerts held back for a later drain, in requeue order: ``(ready_at, alert)``.
        self._retry_queue: list[tuple[float, IncomingAlert]] = []
        self._maxsize = maxsize
        self._retry_after = retry_after
        self._dropped: int = 0
        self._lock = threading.Lock()
        self._pending_event = threading.Event()  # Set when alerts are available

    def put(self, alert: IncomingAlert) -> bool:
        """Return True if queued without eviction, False if an old alert was dropped."""
        with self._lock:
            if len(self._queue) + len(self._retry_queue) >= self._maxsize:
                self._evict_locked()
                self._queue.append(alert)
                self._pending_event.set()
                return False
            self._queue.append(alert)
            self._pending_event.set()
        return True

    def requeue(self, alert: IncomingAlert) -> None:
        """Hold a not-yet-processed alert for a later drain instead of losing it.

        The retry deliberately does *not* set the pending event: a failure that
        keeps failing would otherwise make a drain loop spin on a permanently set
        event. A waiter with a timed ``wait()`` still revisits it once due.
        """
        with self._lock:
            if len(self._queue) + len(self._retry_queue) >= self._maxsize:
                self._evict_locked()
            self._retry_queue.append((time.monotonic() + self._retry_after, alert))

    def _evict_locked(self) -> None:
        """Drop one alert to make room; the caller holds the lock.

        Ready items go first even when a retry is older: a retry already failed
        once, so evicting it twice as fast would only push the queue toward losing
        alerts that were never attempted.
        """
        if self._queue:
            self._queue.popleft()
        else:
            self._retry_queue.pop(0)
        self._dropped += 1

    def pop_nowait(self) -> IncomingAlert | None:
        with self._lock:
            try:
                return self._queue.popleft()
            except IndexError:
                return None

    def iter_pending(self) -> list[IncomingAlert]:
        """Take every alert ready now, leaving retries that are not due still queued."""
        now = time.monotonic()
        with self._lock:
            items: list[IncomingAlert] = list(self._queue)
            self._queue.clear()
            not_due: list[tuple[float, IncomingAlert]] = []
            for ready_at, alert in self._retry_queue:
                if ready_at <= now:
                    items.append(alert)
                else:
                    not_due.append((ready_at, alert))
            self._retry_queue = not_due
            if not self._queue:
                self._pending_event.clear()
            return items

    def peek_last(self, n: int) -> list[IncomingAlert]:
        with self._lock:
            items = list(self._queue)
            return items[-n:]

    @property
    def qsize(self) -> int:
        """Alerts still owed a drain, including those waiting out a retry delay."""
        return len(self._queue) + len(self._retry_queue)

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def pending_event(self) -> threading.Event:
        """Event set when alerts are available, for background wakers."""
        return self._pending_event


_current_inbox: AlertInbox | None = None


def set_current_inbox(inbox: AlertInbox | None) -> None:
    global _current_inbox
    _current_inbox = inbox


def get_current_inbox() -> AlertInbox | None:
    return _current_inbox


__all__ = [
    "AlertInbox",
    "IncomingAlert",
    "get_current_inbox",
    "set_current_inbox",
]
