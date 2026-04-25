"""Shared availability and error response helpers for tools."""

from __future__ import annotations

from typing import Any


def unavailable(source: str, empty_key: str, error: str, **extra: Any) -> dict[str, Any]:
    """Standardised unavailable response for tools.

    Ensures a consistent shape for 'available: False' responses, including
    source identifier, error message, and an empty list for the primary
    result key to keep downstream parsers happy.
    """
    return {"source": source, "available": False, "error": error, empty_key: [], **extra}
