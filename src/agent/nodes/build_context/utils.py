"""Utility functions for context building."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

TIMEOUT = 10.0


@dataclass(frozen=True)
class CallOutcome:
    result: Any | None
    error: str | None
    duration_ms: int
    attempts: int

    @property
    def ok(self) -> bool:
        return self.error is None


def call_safe(
    fn,
    *,
    timeout: float = TIMEOUT,
    retries: int = 0,
    retry_delay: float = 0.0,
    **kwargs,
) -> CallOutcome:
    """Call function with timeout and optional retries."""
    start = time.monotonic()
    attempts = 0
    last_error: str | None = None

    for attempt in range(retries + 1):
        attempts += 1
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                result = ex.submit(fn, **kwargs).result(timeout=timeout)
                duration_ms = int((time.monotonic() - start) * 1000)
                return CallOutcome(
                    result=result,
                    error=None,
                    duration_ms=duration_ms,
                    attempts=attempts,
                )
            except FuturesTimeoutError:
                last_error = f"Timeout after {timeout}s"
            except Exception as err:
                last_error = str(err)

        if attempt < retries and retry_delay > 0:
            time.sleep(retry_delay)

    duration_ms = int((time.monotonic() - start) * 1000)
    return CallOutcome(
        result=None,
        error=last_error or "Unknown error",
        duration_ms=duration_ms,
        attempts=attempts,
    )
