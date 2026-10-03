"""Low-level terminal key reader for TTY-first interactive menus.

Shared by :mod:`choice_menu` (REPL inline pickers) and the Ask User wizard so
the raw-mode terminal I/O lives in one place.

Return values from :func:`read_key_unix` / :func:`read_key_windows`:
  ``"up"``, ``"down"``, ``"enter"``, ``"cancel"``, ``"tab"``,
  ``"shift_tab"``, ``"right"``, ``"left"``, ``"1"``–``"9"``, ``"eof"``,
  ``"ignore"``.

Only ``"cancel"`` and ``"eof"`` dismiss a menu; readers that take
``on_dismiss`` report the ``DismissKey`` behind them. On POSIX an ESC that
starts a terminal reply or an unmapped key sequence reads as ``"ignore"``, never
``"cancel"`` (see :func:`_classify_escape_unix`).
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
import time
from collections.abc import Callable
from typing import Literal

# Key class that dismissed a menu: Esc, Ctrl-C, Ctrl-D, ``q``, a caller shortcut, or EOF.
DismissKey = Literal["esc", "ctrl_c", "ctrl_d", "q", "shortcut", "eof"]
OnDismiss = Callable[[DismissKey], None]

# A lone Esc waits this long for the rest of an escape sequence before it cancels.
_ESC_FOLLOW_UP_S = 0.1
# Bounds for consuming one escape sequence once its introducer has arrived.
_SEQUENCE_TIMEOUT_S = 0.1
_SEQUENCE_MAX_BYTES = 64

_BEL = 0x07
_ESC = 0x1B
# ECMA-48 final bytes end a CSI or SS3 sequence; parameter bytes come before them.
_FINAL_BYTE_MIN = 0x40
_FINAL_BYTE_MAX = 0x7E
# Keys named by ``ESC [`` / ``ESC O`` plus a bare final byte; any other sequence is ignored.
_CSI_KEYS: dict[int, str] = {
    ord("A"): "up",
    ord("B"): "down",
    ord("C"): "right",
    ord("D"): "left",
    ord("Z"): "shift_tab",
}
_SS3_KEYS: dict[int, str] = {
    ord("A"): "up",
    ord("B"): "down",
    ord("C"): "right",
    ord("D"): "left",
}
# OSC, DCS, SOS, PM and APC introducers: a string ended by BEL or ST (``ESC \``).
_STRING_INTRODUCERS = frozenset(b"]PX^_")


def flush_stdin_unix() -> None:
    """Discard pending stdin bytes before raw-mode reading."""
    with contextlib.suppress(Exception):
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)  # type: ignore[attr-defined]


def flush_pending_input() -> None:
    """Drop leftover keypresses (Enter from the previous prompt, CPR, etc.).

    Ask User must not treat the newline that submitted the last prompt — or
    the ``/choose`` autosubmit — as answering every question with option 1.
    """
    flush_stdin_unix()
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import msvcrt  # type: ignore[import,attr-defined]

        while msvcrt.kbhit():  # type: ignore[attr-defined]
            msvcrt.getwch()  # type: ignore[attr-defined]


def _raw_input_mode(fd: int) -> None:
    """Raw keystrokes with output left cooked: a line feed still returns the carriage.

    ``tty.setraw`` also clears OPOST. A termios snapshot taken while a key read
    is in flight restores that later, and everything painted after it walks
    across the screen one column further per line.
    """
    import termios
    import tty

    tty.setraw(fd)  # type: ignore[attr-defined]
    attrs = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    attrs[1] |= termios.OPOST  # type: ignore[attr-defined]
    termios.tcsetattr(fd, termios.TCSANOW, attrs)  # type: ignore[attr-defined]


def restore_stdin_terminal() -> None:
    """Return stdin to canonical echo mode after Live/raw progress UI.

    Progress rendering uses a background Tab watcher that puts stdin in
    non-canonical mode without echo. If nested watchers restore the wrong
    snapshot, the shell prompt appears to accept input but characters are not
    echoed. Call this after progress UI teardown and before line prompts.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        return
    import termios

    with contextlib.suppress(Exception):
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        # Restore cooked-mode flags a raw menu clears: ICRNL so Enter (CR) submits,
        # OPOST for output newlines, ICANON/ECHO/ISIG for line editing and signals.
        attrs[0] |= termios.BRKINT | termios.ICRNL | termios.IXON  # type: ignore[attr-defined]
        attrs[1] |= termios.OPOST  # type: ignore[attr-defined]
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG  # type: ignore[attr-defined]
        if hasattr(termios, "IEXTEN"):
            attrs[3] |= termios.IEXTEN  # type: ignore[attr-defined]
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)  # type: ignore[attr-defined]
        termios.tcflush(fd, termios.TCIFLUSH)  # type: ignore[attr-defined]


def _alpha_option_key(byte: int) -> str | None:
    """Uppercase letter for an ascii-letter byte, else ``None``.

    Used by letter-select menus (the Ask User clarification picker), where an
    option is chosen by its ``(A)``/``(B)`` letter instead of a digit.
    """
    char = chr(byte)
    return char.upper() if char.isascii() and char.isalpha() else None


def _dismiss(on_dismiss: OnDismiss | None, key: DismissKey) -> str:
    """Report ``key`` to ``on_dismiss``; return ``"eof"`` for EOF, else ``"cancel"``."""
    if on_dismiss is not None:
        on_dismiss(key)
    return "eof" if key == "eof" else "cancel"


def _read_byte_before(fd: int, deadline: float) -> int | None:
    """Next byte from ``fd`` when one is ready by ``deadline`` (monotonic), else ``None``."""
    if not select.select([fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
        return None
    data = os.read(fd, 1)
    return data[0] if data else None


def _consume_sequence(fd: int, keys: dict[int, str]) -> str:
    """Read a CSI or SS3 body through its final byte; only a bare final byte names a key."""
    deadline = time.monotonic() + _SEQUENCE_TIMEOUT_S
    for position in range(_SEQUENCE_MAX_BYTES):
        byte = _read_byte_before(fd, deadline)
        if byte is None:
            return "ignore"
        if _FINAL_BYTE_MIN <= byte <= _FINAL_BYTE_MAX:
            return keys.get(byte, "ignore") if position == 0 else "ignore"
    return "ignore"


def _consume_string(fd: int) -> None:
    """Read an OSC/DCS-style string through BEL or ST (``ESC \\``)."""
    deadline = time.monotonic() + _SEQUENCE_TIMEOUT_S
    for _ in range(_SEQUENCE_MAX_BYTES):
        byte = _read_byte_before(fd, deadline)
        if byte is None or byte == _BEL:
            return
        if byte == _ESC:
            _read_byte_before(fd, deadline)  # the backslash that completes ST
            return


def _classify_escape_unix(fd: int) -> str:
    """Classify the input after an ESC byte: ``"cancel"``, an arrow token, or ``"ignore"``.

    Only a lone Esc (nothing within 100 ms) or Esc Esc cancels. Terminal
    replies (CPR, DA, DECRQM, focus, OSC colours), mouse reports, unmapped keys
    such as Home or PgUp, and Option/Meta chords are read through their final
    byte, bounded to 64 bytes / 100 ms, and ignored so no tail is left behind
    to be read as keystrokes.
    """
    introducer = _read_byte_before(fd, time.monotonic() + _ESC_FOLLOW_UP_S)
    if introducer is None or introducer == _ESC:
        return "cancel"
    if introducer == ord("["):
        return _consume_sequence(fd, _CSI_KEYS)
    if introducer == ord("O"):
        return _consume_sequence(fd, _SS3_KEYS)
    if introducer in _STRING_INTRODUCERS:
        _consume_string(fd)
    return "ignore"


def read_key_unix(
    *,
    also_cancel: tuple[bytes, ...] = (),
    space_confirms: bool = True,
    alpha_keys: bool = False,
    on_dismiss: OnDismiss | None = None,
) -> str:
    """Read one logical keypress in raw mode; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"shift_tab"``, ``"right"``, ``"left"``,
    ``"1"``–``"9"``, ``"eof"``, ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"`` (e.g.
    ``(b"s", b"S")`` for an explicit skip shortcut). Escape sequences other
    than arrows and Shift+Tab read as ``"ignore"`` (see
    :func:`_classify_escape_unix`). ``on_dismiss`` is told which key produced
    ``"cancel"`` or ``"eof"`` before it is returned.
    """
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        _raw_input_mode(fd)
        ch = os.read(fd, 1)
        if not ch:
            return _dismiss(on_dismiss, "eof")
        b = ch[0]
        if b == 3:  # Ctrl-C
            return _dismiss(on_dismiss, "ctrl_c")
        if b == 4:  # Ctrl-D
            return _dismiss(on_dismiss, "ctrl_d")
        if ch in also_cancel:
            return _dismiss(on_dismiss, "shortcut")
        if b in (10, 13) or (space_confirms and b == 32):  # LF / CR / optional Space
            return "enter"
        if b == 9:  # Tab
            return "tab"
        if alpha_keys:
            letter = _alpha_option_key(b)
            if letter is not None:  # (A)/(B)/… select; arrows still navigate
                return letter
        elif 0x31 <= b <= 0x39:  # 1-9
            return chr(b)
        if not alpha_keys and ch in (b"j", b"J"):
            return "down"
        if not alpha_keys and ch in (b"k", b"K"):
            return "up"
        if not alpha_keys and ch in (b"q", b"Q"):
            return _dismiss(on_dismiss, "q")
        if b == _ESC:
            key = _classify_escape_unix(fd)
            return _dismiss(on_dismiss, "esc") if key == "cancel" else key
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def read_key_windows(
    *,
    also_cancel: tuple[bytes, ...] = (),
    space_confirms: bool = True,
    alpha_keys: bool = False,
    on_dismiss: OnDismiss | None = None,
) -> str:
    """Read one logical keypress on Windows; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"shift_tab"``, ``"right"``, ``"left"``,
    ``"1"``–``"9"``, ``"eof"``, ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"``;
    ``on_dismiss`` is told which key produced ``"cancel"``.
    """
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch == b"\x03":
        return _dismiss(on_dismiss, "ctrl_c")
    if ch == b"\x1b":
        return _dismiss(on_dismiss, "esc")
    if ch in also_cancel:
        return _dismiss(on_dismiss, "shortcut")
    if ch in (b"\r", b"\n") or (space_confirms and ch == b" "):
        return "enter"
    if ch == b"\t":
        return "tab"
    if alpha_keys and len(ch) == 1:
        letter = _alpha_option_key(ch[0])
        if letter is not None:  # (A)/(B)/… select; arrows still navigate
            return letter
    elif len(ch) == 1 and b"1" <= ch <= b"9":
        return str(ch.decode("ascii"))
    if not alpha_keys and ch in (b"j", b"J"):
        return "down"
    if not alpha_keys and ch in (b"k", b"K"):
        return "up"
    if not alpha_keys and ch in (b"q", b"Q"):
        return _dismiss(on_dismiss, "q")
    if ch in (b"\xe0", b"\x00"):
        ch2 = msvcrt.getch()  # type: ignore[attr-defined]
        if ch2 == b"H":
            return "up"
        if ch2 == b"P":
            return "down"
        if ch2 == b"M":
            return "right"
        if ch2 == b"K":
            return "left"
        if ch2 == b"\x0f":
            return "shift_tab"
        return "ignore"
    return "ignore"


def read_typing_key() -> str:
    """Read one key while editing free text inside a menu row.

    Returns ``"enter"``, ``"cancel"``, ``"backspace"``, ``"eof"``, ``"ignore"``,
    or a single printable character (ASCII). Multi-byte UTF-8 is ignored for
    now so the Ask User custom row stays a simple in-place field.
    """
    if os.name == "nt":
        return _read_typing_key_windows()
    return _read_typing_key_unix()


def read_menu_or_char(
    *,
    allow_chars: bool = False,
    alpha_keys: bool = False,
    on_dismiss: OnDismiss | None = None,
) -> str:
    """Menu navigation keys, optionally plus printable chars / backspace.

    When ``allow_chars`` is True (custom option row focused), typing inserts
    on that row in place; arrows/tab still move between options. When
    ``alpha_keys`` is True (and not typing), an option is selected by its
    ``(A)``/``(B)`` letter instead of a digit. ``on_dismiss`` is told which key
    produced ``"cancel"`` or ``"eof"``.
    """
    if os.name == "nt":
        return _read_menu_or_char_windows(
            allow_chars=allow_chars, alpha_keys=alpha_keys, on_dismiss=on_dismiss
        )
    return _read_menu_or_char_unix(
        allow_chars=allow_chars, alpha_keys=alpha_keys, on_dismiss=on_dismiss
    )


def _read_menu_or_char_unix(
    *,
    allow_chars: bool,
    alpha_keys: bool = False,
    on_dismiss: OnDismiss | None = None,
) -> str:
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        _raw_input_mode(fd)
        ch = os.read(fd, 1)
        if not ch:
            return _dismiss(on_dismiss, "eof")
        b = ch[0]
        if b == 3:  # Ctrl-C
            return _dismiss(on_dismiss, "ctrl_c")
        if b == 4:  # Ctrl-D
            return _dismiss(on_dismiss, "ctrl_d")
        if b in (10, 13):
            return "enter"
        if allow_chars and b in (8, 127):
            return "backspace"
        if b == 9:
            return "tab"
        # Letters select in alpha mode; otherwise digits stay as select
        # shortcuts unless typing on the custom row.
        if alpha_keys and not allow_chars:
            letter = _alpha_option_key(b)
            if letter is not None:
                return letter
        elif not allow_chars and 0x31 <= b <= 0x39:
            return chr(b)
        if not alpha_keys and ch in (b"j", b"J") and not allow_chars:
            return "down"
        if not alpha_keys and ch in (b"k", b"K") and not allow_chars:
            return "up"
        if not alpha_keys and ch in (b"q", b"Q") and not allow_chars:
            return _dismiss(on_dismiss, "q")
        if b == _ESC:
            key = _classify_escape_unix(fd)
            return _dismiss(on_dismiss, "esc") if key == "cancel" else key
        # Space: printable when typing; otherwise a distinct toggle key for multi-select.
        if b == 32:
            return " "
        if allow_chars and 32 <= b <= 126:
            return chr(b)
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def _read_menu_or_char_windows(
    *,
    allow_chars: bool,
    alpha_keys: bool = False,
    on_dismiss: OnDismiss | None = None,
) -> str:
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch == b"\x03":
        return _dismiss(on_dismiss, "ctrl_c")
    if ch in (b"\r", b"\n"):
        return "enter"
    if allow_chars and ch in (b"\x08", b"\x7f"):
        return "backspace"
    if ch == b"\t":
        return "tab"
    if ch == b"\x1b":
        return _dismiss(on_dismiss, "esc")
    if alpha_keys and not allow_chars and len(ch) == 1:
        letter = _alpha_option_key(ch[0])
        if letter is not None:
            return letter
    elif not allow_chars and len(ch) == 1 and b"1" <= ch <= b"9":
        return str(ch.decode("ascii"))
    if not alpha_keys and not allow_chars and ch in (b"j", b"J"):
        return "down"
    if not alpha_keys and not allow_chars and ch in (b"k", b"K"):
        return "up"
    if not alpha_keys and not allow_chars and ch in (b"q", b"Q"):
        return _dismiss(on_dismiss, "q")
    if ch in (b"\xe0", b"\x00"):
        ch2 = msvcrt.getch()  # type: ignore[attr-defined]
        if ch2 == b"H":
            return "up"
        if ch2 == b"P":
            return "down"
        if ch2 == b"M":
            return "right"
        if ch2 == b"K":
            return "left"
        if ch2 == b"\x0f":
            return "shift_tab"
        return "ignore"
    if ch == b" ":
        return " "
    if allow_chars:
        try:
            text = ch.decode("ascii")
        except UnicodeDecodeError:
            return "ignore"
        if text.isprintable() and text != "\t":
            return str(text)
    return "ignore"


def _read_typing_key_unix() -> str:
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        _raw_input_mode(fd)
        ch = os.read(fd, 1)
        if not ch:
            return "eof"
        b = ch[0]
        if b in (3, 4):  # Ctrl-C / Ctrl-D
            return "cancel"
        if b in (10, 13):
            return "enter"
        if b in (8, 127):  # BS / DEL
            return "backspace"
        if b == _ESC:
            # Esc alone cancels; any sequence (arrows included) is consumed whole.
            return "cancel" if _classify_escape_unix(fd) == "cancel" else "ignore"
        if 32 <= b <= 126:
            return chr(b)
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def _read_typing_key_windows() -> str:
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch in (b"\x03", b"\x1b"):
        return "cancel"
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch in (b"\x08", b"\x7f"):
        return "backspace"
    if ch in (b"\xe0", b"\x00"):
        msvcrt.getch()  # type: ignore[attr-defined]
        return "ignore"
    try:
        text = ch.decode("ascii")
    except UnicodeDecodeError:
        return "ignore"
    if text.isprintable() and text != "\t":
        return str(text)
    return "ignore"


__all__ = [
    "DismissKey",
    "OnDismiss",
    "flush_pending_input",
    "flush_stdin_unix",
    "read_key_unix",
    "read_key_windows",
    "read_menu_or_char",
    "read_typing_key",
    "restore_stdin_terminal",
]
