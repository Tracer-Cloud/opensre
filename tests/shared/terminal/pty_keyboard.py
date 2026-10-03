"""A real pty as stdin whose keystrokes arrive only once a key read is in raw mode.

The key reader enters raw mode with ``TCSAFLUSH``, which discards input typed
earlier, so each queued chunk is written right after that switch: one chunk per
key read, the way a terminal reply or a keypress lands mid-read.
"""

from __future__ import annotations

import io
import os
import select
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from surfaces.shared.terminal.components import key_reader


class TtyStringIO(io.StringIO):
    """Captured stdout that reports itself as a terminal."""

    def isatty(self) -> bool:
        return True


class PtyKeyboard:
    """Types queued chunks into the pty, one per raw-mode key read."""

    def __init__(self, master: int, slave: int) -> None:
        self._master = master
        self._slave = slave
        self._queued: list[bytes] = []

    def queue(self, *chunks: bytes) -> None:
        """Type each chunk when a later key read enters raw mode."""
        self._queued.extend(chunks)

    def type_next(self) -> None:
        if self._queued:
            os.write(self._master, self._queued.pop(0))

    def unread(self) -> bytes:
        """Bytes the last key read left in the terminal input queue."""
        left = b""
        while select.select([self._slave], [], [], 0)[0]:
            left += os.read(self._slave, 64)
        return left


def _tty_stdin(fd: int) -> SimpleNamespace:
    return SimpleNamespace(fileno=lambda: fd, isatty=lambda: True)


@contextmanager
def pty_stdin(monkeypatch: pytest.MonkeyPatch) -> Iterator[PtyKeyboard]:
    """Point ``sys.stdin`` at a fresh pty; skip where POSIX terminals are unavailable."""
    termios = pytest.importorskip("termios")
    pty = pytest.importorskip("pty")
    tty = pytest.importorskip("tty")
    master, slave = pty.openpty()
    try:
        # Non-canonical from the start, so a byte a read leaves behind is still
        # readable after the reader restores its termios snapshot.
        tty.setraw(slave, termios.TCSANOW)
        keyboard = PtyKeyboard(master, slave)
        enter_raw_mode = key_reader._raw_input_mode

        def _enter_raw_mode_then_type(fd: int) -> None:
            enter_raw_mode(fd)
            keyboard.type_next()

        monkeypatch.setattr(sys, "stdin", _tty_stdin(slave))
        monkeypatch.setattr(key_reader, "_raw_input_mode", _enter_raw_mode_then_type)
        yield keyboard
    finally:
        os.close(master)
        os.close(slave)
