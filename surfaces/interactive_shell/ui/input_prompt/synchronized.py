"""Synchronized terminal output (DECSET 2026) for multi-step repaints.

A repaint that erases before it draws is two visible states, not one: the
terminal shows the gap. On a resize burst that reads as the Auto bar and the
composer flickering once per signal.

Terminals that implement synchronized output hold every write between the
begin and end markers off-screen and present them as one frame. Terminals
that do not simply ignore both private-mode toggles, so the repaint stays
correct and only loses the flicker-free presentation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SYNCED_OUTPUT_START = "\x1b[?2026h"
SYNCED_OUTPUT_END = "\x1b[?2026l"


@contextmanager
def synchronized_output(output: Any) -> Iterator[None]:
    """Present everything written inside the block as a single frame."""
    output.write_raw(SYNCED_OUTPUT_START)
    output.flush()
    try:
        yield
    finally:
        output.write_raw(SYNCED_OUTPUT_END)
        output.flush()


__all__ = ["SYNCED_OUTPUT_END", "SYNCED_OUTPUT_START", "synchronized_output"]
