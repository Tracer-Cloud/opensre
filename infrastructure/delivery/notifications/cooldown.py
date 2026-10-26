"""Shared per-key cooldown/throttle gate for alarm dispatchers.

Notification dispatchers use this primitive to suppress repeat deliveries for
the same key within a cooldown window, leaving each dispatcher responsible only
for its own transport.
"""

from __future__ import annotations

import threading


class CooldownGate:
    """Thread-safe per-key cooldown gate.

    ``try_reserve`` reserves the cooldown slot for a key *before* the caller's
    network call, so a concurrent dispatch on the same key sees the
    reservation and is suppressed. Without this, two threads could both pass
    the check (state of "last dispatched" at check time != use time, classic
    TOCTOU) and both send.
    """

    def __init__(self, cooldown_seconds: float) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._last_dispatched: dict[str, float] = {}
        self._lock = threading.Lock()

    def try_reserve(self, key: str, now: float) -> float | None:
        """Reserve *key* at time *now* unless it is still in cooldown.

        Returns ``None`` (and reserves the slot) when the key is not in
        cooldown. Returns the remaining cooldown time in seconds, without
        reserving, when the key is still suppressed.
        """
        with self._lock:
            last = self._last_dispatched.get(key)
            if last is not None:
                remaining = self._cooldown_seconds - (now - last)
                if remaining > 0:
                    return remaining
            self._last_dispatched[key] = now
            return None
