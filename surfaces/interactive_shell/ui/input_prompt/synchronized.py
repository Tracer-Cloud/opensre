"""Synchronized terminal output (DECSET 2026) for multi-step repaints.

A terminal that implements the mode holds every write between the begin and
end markers off-screen and presents them as one frame, so an erase and the
redraw that replaces it never show as two states. Only VT-capable outputs
receive the markers; native Win32 output would print them as text.

The mode does not nest: a second end marker presents whatever has been
written, whoever wrote it. Only one writer may hold a frame at a time, and it
must close its own — hence a block that restores the mode on every exit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from prompt_toolkit.output.vt100 import Vt100_Output

SYNCED_OUTPUT_START = "\x1b[?2026h"
SYNCED_OUTPUT_END = "\x1b[?2026l"


def supports_synchronized_output(output: Any) -> bool:
    """Return whether the output safely accepts VT private-mode sequences."""
    if isinstance(output, Vt100_Output):
        return True
    return isinstance(getattr(output, "vt100_output", None), Vt100_Output)


@contextmanager
def synchronized_output(output: Any, *, enabled: bool = True) -> Iterator[None]:
    """Present everything written inside the block as a single frame.

    ``enabled=False`` runs the block unframed, for callers that cannot finish
    the repaint inside it — a frame closed over a half-done repaint presents
    the gap it was meant to hide.
    """
    if not enabled or not supports_synchronized_output(output):
        yield
        return
    output.write_raw(SYNCED_OUTPUT_START)
    output.flush()
    try:
        yield
    finally:
        output.write_raw(SYNCED_OUTPUT_END)
        output.flush()


__all__ = [
    "SYNCED_OUTPUT_END",
    "SYNCED_OUTPUT_START",
    "supports_synchronized_output",
    "synchronized_output",
]
