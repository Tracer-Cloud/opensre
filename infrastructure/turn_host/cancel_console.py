"""Gateway console wrapper: Rich console + ``cancel_requested`` (shell parity).

One Event per turn (``output.turn_cancel``). Soft timeout and ``/stop``
(:class:`~gateway.core.middleware.active_turns.ActiveTurnRegistry`) both ``set()``
it. :class:`TurnRunner` binds this wrapper so tools and ReAct see
``cancel_requested`` like the interactive shell's ``StreamingConsole``.

The Event itself is created/attached by
:func:`core.agent_harness.spi.cancel.ensure_turn_cancel`.
"""

from __future__ import annotations

import threading
from typing import Any

from rich.console import Console

# Set on the wrapper itself; everything else is proxied to the wrapped console.
_OWN_ATTRIBUTES = frozenset({"_output", "_cancel_event"})


class CancelConsole:
    """Console stand-in that exposes ``cancel_requested`` from a shared Event.

    Delegates rendering to the gateway pool's Rich console so tool observers and
    subprocess presenters keep working; only cancellation is added.

    Reads *and writes* are proxied. Slash capture turns recording on
    (``console.record = True``) and then calls ``export_text`` on the same
    object: if the write stopped at the wrapper, recording would stay off on
    the wrapped console and every slash command on a chat transport would fail.
    """

    def __init__(self, output: Console, cancel_event: threading.Event) -> None:
        self._output = output
        self._cancel_event = cancel_event

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def print(self, *args: Any, **kwargs: Any) -> None:
        self._output.print(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._output, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWN_ATTRIBUTES:
            super().__setattr__(name, value)
            return
        setattr(self._output, name, value)


__all__ = ["CancelConsole"]
