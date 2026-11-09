"""Signal-to-cancellation mapping for one-shot ``opensre ask`` invocations."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any


class AskSignal(BaseException):
    """Signal raised inside the command so session cleanup can finish."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@contextmanager
def ask_signal_scope(cancel_event: threading.Event | None = None) -> Iterator[None]:
    """Map process termination signals to ``AskSignal`` within one CLI scope."""
    event = cancel_event or threading.Event()
    previous: dict[signal.Signals, Any] = {}

    def handle(signum: int, _frame: FrameType | None) -> None:
        event.set()
        raise AskSignal(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, handle)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


__all__ = ["AskSignal", "ask_signal_scope"]
