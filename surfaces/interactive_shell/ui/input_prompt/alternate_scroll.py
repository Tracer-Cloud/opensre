"""Terminal alternate-scroll control for the full-screen composer.

The composer runs with ``mouse_support=False`` so the terminal keeps native
drag-select over the transcript (see ``session.build_prompt_session``). With
mouse reporting off, terminals in the alternate screen fall back to *alternate
scroll* (DECSET 1007) and translate wheel notches into cursor-key sequences.
prompt_toolkit reads those as Up/Down, and ``Buffer.auto_up`` replaces the
composer contents with a history entry — so scrolling the transcript pastes old
prompts into the input box. Turning the mode off makes the wheel inert instead
of destructive; PageUp/PageDown still page the transcript.

Restore writes the mode back *on*, which is the default in every terminal that
implements it (VTE, Ghostty, kitty, iTerm2, Alacritty, Windows Terminal).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

ALTERNATE_SCROLL_OFF = "\x1b[?1007l"
ALTERNATE_SCROLL_ON = "\x1b[?1007h"


@contextmanager
def alternate_scroll_disabled(stream: TextIO | None = None) -> Iterator[None]:
    """Stop the terminal from synthesising cursor keys from wheel events."""
    out = stream if stream is not None else sys.stdout
    if not (hasattr(out, "isatty") and out.isatty()):
        yield
        return
    out.write(ALTERNATE_SCROLL_OFF)
    out.flush()
    try:
        yield
    finally:
        out.write(ALTERNATE_SCROLL_ON)
        out.flush()


__all__ = ["ALTERNATE_SCROLL_OFF", "ALTERNATE_SCROLL_ON", "alternate_scroll_disabled"]
