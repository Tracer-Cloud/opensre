"""Small process-local readiness state for Gateway startup dependencies."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_ready = False


def set_ready(ready: bool) -> None:
    """Publish whether mandatory Gateway startup dependencies are healthy."""
    global _ready
    with _lock:
        _ready = ready


def is_gateway_ready() -> bool:
    """Return the current Gateway readiness state."""
    with _lock:
        return _ready


__all__ = ["is_gateway_ready", "set_ready"]
