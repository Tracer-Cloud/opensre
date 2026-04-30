"""Shared coercion helpers for config and alert parsing."""

from __future__ import annotations

from typing import Any


def safe_int(value: Any, default: int) -> int:
    """Return ``value`` coerced to ``int`` or ``default`` on bad input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
