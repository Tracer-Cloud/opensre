"""Alternate-scroll control for the full-screen composer.

Clearing DECSET 1007 stops the terminal translating wheel notches into cursor
keys, which the composer reads as Up and answers with history recall. The
composer asks for mouse reporting (see ``session.build_prompt_session``), which
pre-empts the fallback wherever it arrives; this covers the environments where it
never does, such as tmux with ``mouse off``.

Applies only to a tty, and only for the lifetime of the context.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

# XTSAVE/XTRESTORE bracket the reset. The mode's initial value is configurable and
# defaults to off in xterm, so exit has to put back what the terminal had rather
# than assert a value of its own.
ALTERNATE_SCROLL_SAVE = "\x1b[?1007s"
ALTERNATE_SCROLL_OFF = "\x1b[?1007l"
ALTERNATE_SCROLL_RESTORE = "\x1b[?1007r"


@contextmanager
def alternate_scroll_disabled(stream: TextIO | None = None) -> Iterator[None]:
    """Stop the terminal synthesising cursor keys from wheel events."""
    out = stream if stream is not None else sys.stdout
    if not (hasattr(out, "isatty") and out.isatty()):
        yield
        return
    out.write(ALTERNATE_SCROLL_SAVE + ALTERNATE_SCROLL_OFF)
    out.flush()
    try:
        yield
    finally:
        out.write(ALTERNATE_SCROLL_RESTORE)
        out.flush()


__all__ = [
    "ALTERNATE_SCROLL_OFF",
    "ALTERNATE_SCROLL_RESTORE",
    "ALTERNATE_SCROLL_SAVE",
    "alternate_scroll_disabled",
]
