"""In-flight turn cancel registry for gateway chat hosts.

Transports serialize turns per conversation, so a ``/stop`` message cannot
wait behind the same lock as the running turn. Instead each accepted turn
registers its ``turn_cancel`` Event here under a conversation key;
``/stop`` looks the key up **outside** the turn lock, sets every Event
registered for it, and optionally runs a transport-supplied finalize
callback (claim outcome + ``Stopped.`` copy).

Transports that dispatch turns as detached tasks must register at dispatch
time — before the task first runs — or a ``/stop`` in the same poll batch
finds nothing to cancel while the accepted turn proceeds. A key may hold
several turns at once (one running, others queued behind the conversation
lock); stopping the conversation cancels them all.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

_STOP_TOKENS = frozenset({"/stop", "stop", "/cancel", "cancel"})


@dataclass(slots=True)
class _TrackedTurn:
    event: threading.Event
    on_user_stop: Callable[[], None] | None = None


class ActiveTurnRegistry:
    """Thread-safe map of conversation key → in-flight cancel Events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: dict[str, list[_TrackedTurn]] = {}

    def register(
        self,
        key: str,
        event: threading.Event,
        *,
        on_user_stop: Callable[[], None] | None = None,
    ) -> None:
        """Publish ``event`` for ``key``; pair with :meth:`unregister`."""
        with self._lock:
            self._turns.setdefault(key, []).append(
                _TrackedTurn(event=event, on_user_stop=on_user_stop)
            )

    def unregister(self, key: str, event: threading.Event) -> None:
        """Remove ``event`` from ``key``; unknown pairs are a no-op."""
        with self._lock:
            turns = self._turns.get(key)
            if turns is None:
                return
            remaining = [turn for turn in turns if turn.event is not event]
            if remaining:
                self._turns[key] = remaining
            else:
                del self._turns[key]

    def bind_user_stop(
        self,
        key: str,
        event: threading.Event,
        on_user_stop: Callable[[], None],
    ) -> None:
        """Attach the stop finalizer to an already registered ``event``.

        Dispatch-time registration happens before the turn has an output to
        finalize; the turn binds the callback once it starts. A stop that
        lands in between only sets the Event — the turn must check it before
        invoking the agent.
        """
        with self._lock:
            for turn in self._turns.get(key, []):
                if turn.event is event:
                    turn.on_user_stop = on_user_stop
                    return

    @contextmanager
    def track(
        self,
        key: str,
        event: threading.Event,
        *,
        on_user_stop: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Publish ``event`` for ``key`` for the duration of one turn."""
        self.register(key, event, on_user_stop=on_user_stop)
        try:
            yield
        finally:
            self.unregister(key, event)

    def request_stop(self, key: str) -> bool:
        """Cancel every turn registered for ``key``; return whether any was."""
        with self._lock:
            tracked = list(self._turns.get(key, []))
        for turn in tracked:
            turn.event.set()
            if turn.on_user_stop is not None:
                turn.on_user_stop()
        return bool(tracked)


def is_stop_command(text: str) -> bool:
    """True when the message is a user stop/cancel command (first token)."""
    stripped = text.strip()
    if not stripped:
        return False
    token = stripped.split(maxsplit=1)[0].lower()
    # Telegram: ``/stop@MyBot`` → ``/stop``
    if "@" in token:
        token = token.split("@", 1)[0]
    return token in _STOP_TOKENS


__all__ = [
    "ActiveTurnRegistry",
    "is_stop_command",
]
