"""In-process alert inbox — the domain queue for external alert pushes.

Owns the queue and the process-wide current-inbox handle. HTTP intake is
``POST /alerts`` on the gateway web app.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

from config.strict_config import StrictConfigModel

_DEFAULT_MAX_INBOX = 256


class IncomingAlert(StrictConfigModel):
    text: str
    alert_name: str | None = None
    severity: str | None = None
    source: str | None = None
    received_at: datetime | None = None


class AlertInbox:
    def __init__(self, maxsize: int = _DEFAULT_MAX_INBOX) -> None:
        self._queue: deque[IncomingAlert] = deque()
        self._maxsize = maxsize
        self._dropped: int = 0
        self._lock = threading.Lock()
        self._pending_event = threading.Event()  # Set when alerts are available

    def put(self, alert: IncomingAlert) -> bool:
        """Return True if queued without eviction, False if an old alert was dropped."""
        with self._lock:
            if len(self._queue) >= self._maxsize:
                self._queue.popleft()
                self._dropped += 1
                self._queue.append(alert)
                self._pending_event.set()
                return False
            self._queue.append(alert)
            self._pending_event.set()
        return True

    def pop_nowait(self) -> IncomingAlert | None:
        with self._lock:
            try:
                return self._queue.popleft()
            except IndexError:
                return None

    def iter_pending(self) -> list[IncomingAlert]:
        with self._lock:
            items: list[IncomingAlert] = []
            while True:
                try:
                    items.append(self._queue.popleft())
                except IndexError:
                    break
            if not self._queue:
                self._pending_event.clear()
            return items

    def peek_last(self, n: int) -> list[IncomingAlert]:
        with self._lock:
            items = list(self._queue)
            return items[-n:]

    @property
    def qsize(self) -> int:
        return len(self._queue)

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
