"""CancelConsole must behave like the console it wraps, including writes."""

from __future__ import annotations

import io
import threading

from rich.console import Console

from infrastructure.turn_host.cancel_console import CancelConsole


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_enabling_recording_through_the_wrapper_reaches_the_wrapped_console() -> None:
    """Slash capture sets ``record`` then calls ``export_text`` on the same object.

    ``__getattr__`` only proxies reads, so an unproxied write would leave
    recording off on the wrapped console and make ``export_text`` raise —
    which surfaces as every slash command failing on a chat transport.
    """
    # Arrange.
    wrapped = _console()
    console = CancelConsole(wrapped, threading.Event())

    # Act.
    console.record = True
    console.print("hello from the gateway")

    # Assert: the write landed on the wrapped console, so export works.
    assert wrapped.record is True
    assert "hello from the gateway" in console.export_text()


def test_cancel_requested_tracks_the_shared_event() -> None:
    # Arrange.
    cancel = threading.Event()
    console = CancelConsole(_console(), cancel)

    # Act / Assert.
    assert console.cancel_event is cancel
    assert console.cancel_requested is False
    cancel.set()
    assert console.cancel_requested is True
