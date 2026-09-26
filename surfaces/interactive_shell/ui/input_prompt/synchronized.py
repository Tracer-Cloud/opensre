"""Synchronized terminal output (DECSET 2026) for multi-step repaints.

A repaint that erases before it draws is two visible states, not one: the
terminal shows the gap. On a resize burst that reads as the Auto bar and the
composer flickering once per signal.

Terminals that implement synchronized output hold every write between the
begin and end markers off-screen and present them as one frame. Terminals
that do not simply ignore both private-mode toggles, so the repaint stays
correct and only loses the flicker-free presentation.

The frame is opened and closed by separate calls rather than a context
manager because the two ends belong to different callbacks: a resize opens
it, and the render that finally paints closes it. A repaint can wait on a
cursor-position report, so the redraw call returns before the replacement
prompt exists — closing the frame there would present the erased gap, which
is the flicker this exists to remove. The private mode carries an
implementation timeout for exactly this reason, so a frame that is never
closed resolves itself instead of freezing the display.
"""

from __future__ import annotations

from typing import Any

SYNCED_OUTPUT_START = "\x1b[?2026h"
SYNCED_OUTPUT_END = "\x1b[?2026l"


def begin_synchronized_frame(output: Any) -> None:
    """Start holding writes back until the matching end call."""
    output.write_raw(SYNCED_OUTPUT_START)
    output.flush()


def end_synchronized_frame(output: Any) -> None:
    """Present everything written since the matching begin call."""
    output.write_raw(SYNCED_OUTPUT_END)
    output.flush()


__all__ = [
    "SYNCED_OUTPUT_END",
    "SYNCED_OUTPUT_START",
    "begin_synchronized_frame",
    "end_synchronized_frame",
]
