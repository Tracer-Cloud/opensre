"""Terminal alternate-scroll control for the full-screen composer.

In the alternate screen a terminal with no mouse reporting to forward falls back
to *alternate scroll* (DECSET 1007): it translates wheel notches into cursor-key
sequences. prompt_toolkit reads those as Up at row 0, and ``Buffer.auto_up``
replaces the composer contents with a history entry — so scrolling the transcript
pastes old prompts into the input box.

The composer asks for mouse reporting (see ``session.build_prompt_session``), which
normally pre-empts the fallback. It does not always arrive: under tmux with
``mouse off`` the wheel never reaches us and tmux does the translation itself.
Turning the mode off covers that gap, so the wheel is inert rather than
destructive wherever reporting is unavailable.

Restore writes the mode back *on*, the default in every terminal that implements
it (VTE, Ghostty, kitty, iTerm2, Alacritty, Windows Terminal, tmux).
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
