"""One GitHub client's shared pause while a GitHub rate limit is in force.

GitHub asks a client that hit a rate limit to send nothing until the limit
lifts, and warns that requests sent meanwhile can get the integration banned.
Every request of one client passes this gate, so the pause one response asks
for holds every thread of that client, not only the thread that read it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

PauseNotice = Callable[[float], None]


class RateLimitPauseTooLong(Exception):
    """The pause in force outlasts what the client may still wait; ``seconds`` until it lifts."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"GitHub rate limit lifts in {seconds:.0f}s")
        self.seconds = seconds


class RateLimitGate:
    """Admits a client's requests: holds them through a pause, then one at a time.

    ``patience_seconds`` bounds the pauses the client waits out over its
    lifetime. A pause that would exceed it is not waited at all: every request
    fails at once until it lifts, so a caller learns when to come back instead
    of stalling. A secondary limit means the client sent too much at once, so
    after one the gate admits a single request at a time, as GitHub advises.
    ``on_pause`` hears each pause the client waits out, from the thread that
    hit the limit.
    """

    def __init__(
        self,
        *,
        patience_seconds: float,
        on_pause: PauseNotice | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._patience = patience_seconds
        self._on_pause = on_pause
        self._clock = clock
        self._cond = threading.Condition()
        self._resume_at = 0.0
        self._refused_until = 0.0
        self._waited = 0.0
        self._serial = False
        self._in_flight = 0

    def admit(self) -> None:
        """Block until a request may be sent; every admitted request is ``release``d once.

        Raises ``RateLimitPauseTooLong`` instead of waiting out a pause past
        the client's patience.
        """
        with self._cond:
            while True:
                now = self._clock()
                if now < self._refused_until:
                    raise RateLimitPauseTooLong(self._refused_until - now)
                if now < self._resume_at:
                    self._cond.wait(self._resume_at - now)
                    continue
                if self._serial and self._in_flight:
                    self._cond.wait()
                    continue
                self._in_flight += 1
                return

    def release(self) -> None:
        """Mark one admitted request finished."""
        with self._cond:
            self._in_flight -= 1
            self._cond.notify_all()

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Hold one admission for the ``with`` body; ``admit``'s refusal propagates unadmitted."""
        self.admit()
        try:
            yield
        finally:
            self.release()

    def pause(self, seconds: float, *, secondary: bool) -> bool:
        """Hold every request ``seconds`` from now; False when that outlasts the patience left.

        A pause already in force for at least as long is kept as it is.
        """
        with self._cond:
            now = self._clock()
            resume_at = now + max(seconds, 0.0)
            self._serial = self._serial or secondary
            extension = resume_at - max(self._resume_at, now)
            if extension <= 0:
                return True
            if self._waited + extension > self._patience:
                self._refused_until = max(self._refused_until, resume_at)
                self._cond.notify_all()
                return False
            self._waited += extension
            self._resume_at = resume_at
        if self._on_pause is not None:
            self._on_pause(seconds)
        return True


__all__ = ["PauseNotice", "RateLimitGate", "RateLimitPauseTooLong"]
