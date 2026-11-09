"""Terminal-outcome arbitration and soft deadlines for gateway turns."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class TerminalOutcomeArbiter:
    """Let only the first terminal path update a turn's external state.

    ``cancel_event`` may be supplied by the caller when the Event already
    exists — e.g. registered for ``/stop`` at dispatch time, before the turn
    started — so cancellation requested before construction is not lost.
    """

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._lock = threading.Lock()
        self._claimed = False

    def claim(self) -> bool:
        """Claim the terminal outcome, returning whether this caller won."""
        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True

    @contextmanager
    def timeout_after(
        self,
        timeout_seconds: float,
        on_timeout: Callable[[], None],
    ) -> Iterator[None]:
        """Arm a cooperative timeout for the duration of the context."""

        def _handle_timeout() -> None:
            self.cancel_event.set()
            if self.claim():
                on_timeout()

        timer = threading.Timer(timeout_seconds, _handle_timeout)
        timer.start()
        try:
            yield
        finally:
            timer.cancel()


__all__ = ["TerminalOutcomeArbiter"]
