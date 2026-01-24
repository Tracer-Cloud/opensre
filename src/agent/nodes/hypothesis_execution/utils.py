"""Utility functions for context building."""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

TIMEOUT = 10.0


def call_safe(fn, **kwargs) -> tuple[Any, str | None]:
    """Call function with timeout. Returns (result, error)."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(fn, **kwargs).result(timeout=TIMEOUT), None
        except FuturesTimeoutError:
            return None, f"Timeout after {TIMEOUT}s"
        except Exception as e:
            return None, str(e)
