"""Capture Rich console output without suppressing on-screen rendering."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console


@contextmanager
def capture_console_segment(console: Console) -> Iterator[Callable[[], str]]:
    """Record console output printed inside the block (tee to the real console).

    Uses Rich's ``record`` mode with ``export_text(clear=False)`` so output still
    appears in the REPL while a plain-text slice is available for analytics. The
    recording buffer is cleared on exit when this context enabled recording, so
    long REPL sessions do not accumulate unbounded ``export_text`` history.
    """
    was_recording = console.record
    enabled_recording = not was_recording
    if enabled_recording:
        console.record = True
    start = len(console.export_text(clear=False))
    captured: list[str] = []

    def get_captured() -> str:
        if captured:
            return captured[0]
        return console.export_text(clear=False)[start:].strip()

    try:
        yield get_captured
    finally:
        captured.append(console.export_text(clear=False)[start:].strip())
        if enabled_recording:
            console.export_text(clear=True)
            console.record = False
        else:
            console.record = was_recording
