"""Wall time per phase of a CI repair, for its attempt record."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class PhaseTimer:
    """Monotonic seconds per named phase; a phase entered again adds to its total."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._seconds: dict[str, float] = {}

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time the block under ``name``, also when it raises or returns early."""
        started = self._clock()
        try:
            yield
        finally:
            self._seconds[name] = self._seconds.get(name, 0.0) + (self._clock() - started)

    def take(self) -> dict[str, float]:
        """Return the seconds timed since the last take, in first-seen order, and start over."""
        taken = {name: round(seconds, 3) for name, seconds in self._seconds.items()}
        self._seconds = {}
        return taken


__all__ = ["PhaseTimer"]
