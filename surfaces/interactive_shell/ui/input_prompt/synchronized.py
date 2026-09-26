"""Synchronized terminal output (DECSET 2026) for multi-step repaints.

A repaint that erases before it draws is two visible states, not one: the
terminal shows the gap. On a resize burst that reads as the Auto bar and the
composer flickering once per signal.

Terminals that implement synchronized output hold every write between the
begin and end markers off-screen and present them as one frame. Terminals
that do not simply ignore both private-mode toggles, so the repaint stays
correct and only loses the flicker-free presentation.

The mode does not nest: a second end marker presents whatever has been
written, whoever wrote it. Only one writer may hold a frame at a time, and it
must close its own — hence a block that restores the mode on every exit.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SYNCED_OUTPUT_START = "\x1b[?2026h"
SYNCED_OUTPUT_END = "\x1b[?2026l"


@contextmanager
def synchronized_output(output: Any, *, enabled: bool = True) -> Iterator[None]:
    """Present everything written inside the block as a single frame.

    ``enabled=False`` runs the block unframed, for callers that cannot finish
    the repaint inside it — a frame closed over a half-done repaint presents
    the gap it was meant to hide.
    """
    if not enabled:
        yield
        return
    output.write_raw(SYNCED_OUTPUT_START)
    output.flush()
    try:
        yield
    finally:
        output.write_raw(SYNCED_OUTPUT_END)
        output.flush()


__all__ = ["SYNCED_OUTPUT_END", "SYNCED_OUTPUT_START", "synchronized_output"]
