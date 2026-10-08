"""Share one shutdown timeout across sequential stop steps."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable

from config.constants.gateway import (
    DEFAULT_STOP_TIMEOUT_SECONDS,
    GATEWAY_STOP_TIMEOUT_SECONDS_ENV,
    MAX_STOP_TIMEOUT_SECONDS,
)


class ShutdownBudget:
    """Remaining-time clock for sequential shutdown steps.

    ``take`` returns how long the next step may run. Call ``consume`` after the
    step so later callers see only what is left. Remaining time is never
    negative.
    """

    def __init__(
        self,
        seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._remaining = max(0.0, seconds)
        self._clock = clock

    @property
    def remaining(self) -> float:
        return self._remaining

    def mark(self) -> float:
        """Monotonic timestamp for a later ``consume``."""
        return self._clock()

    def take(self, cap: float | None = None) -> float:
        """Seconds the next step may use; does not consume until ``consume``."""
        if cap is None:
            return self._remaining
        return min(self._remaining, max(0.0, cap))

    def consume(self, started_at: float) -> None:
        """Subtract elapsed time since ``started_at`` from the remaining budget."""
        elapsed = self._clock() - started_at
        self._remaining = max(0.0, self._remaining - max(0.0, elapsed))


def stop_timeout_from_environment() -> float:
    """The SIGTERM budget: the default unless the environment sets a valid one."""
    raw = os.getenv(GATEWAY_STOP_TIMEOUT_SECONDS_ENV, "").strip()
    if not raw:
        return DEFAULT_STOP_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return DEFAULT_STOP_TIMEOUT_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_STOP_TIMEOUT_SECONDS
    return min(seconds, MAX_STOP_TIMEOUT_SECONDS)


__all__ = ["ShutdownBudget", "stop_timeout_from_environment"]
