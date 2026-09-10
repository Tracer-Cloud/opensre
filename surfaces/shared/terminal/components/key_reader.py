"""Low-level terminal key reader for TTY-first interactive menus.

Shared between :mod:`choice_menu` (REPL inline picker) and
:mod:`feedback` (post-run rating prompt) so the raw-mode
terminal I/O lives in one place.

Return values from :func:`read_key_unix` / :func:`read_key_windows`:
  ``"up"``, ``"down"``, ``"enter"``, ``"cancel"``, ``"tab"``,
  ``"shift_tab"``, ``"right"``, ``"left"``, ``"1"``–``"9"``, ``"eof"``,
  ``"ignore"``.
"""

from __future__ import annotations

import contextlib
import os
import sys


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


def read_key_unix(
    *,
    also_cancel: tuple[bytes, ...] = (),
    space_confirms: bool = True,
    alpha_keys: bool = False,
) -> str:
    """Read one logical keypress in raw mode; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"shift_tab"``, ``"right"``, ``"left"``,
    ``"1"``–``"9"``, ``"eof"``, ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"`` (e.g.
    ``(b"s", b"S")`` for an explicit skip shortcut).
    """
    import select as _sel
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        _raw_input_mode(fd)
        ch = os.read(fd, 1)
        if not ch:
            return "eof"
        b = ch[0]
        if b in (3, 4) or ch in also_cancel:  # Ctrl-C / Ctrl-D / caller shortcuts
            return "cancel"
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
            return "cancel"
        if b == 27:  # ESC or arrow-key prefix
            if _sel.select([fd], [], [], 0.1)[0]:
                nxt = os.read(fd, 1)
                if nxt == b"[" and _sel.select([fd], [], [], 0.1)[0]:
                    arr = os.read(fd, 1)
                    if arr == b"A":
                        return "up"
                    if arr == b"B":
                        return "down"
                    if arr == b"C":
                        return "right"
                    if arr == b"D":
                        return "left"
                    if arr == b"Z":
                        return "shift_tab"
                    # Not an arrow key — drain the rest of the CSI sequence so
                    # bytes like "0;1R" from a CPR (ESC[row;colR) don't leak into
                    # the next read or the prompt buffer as literal characters.
                    # The VT/xterm spec defines 0x40–0x7E as valid CSI final bytes.
                    while arr and not (0x40 <= arr[0] <= 0x7E):
                        if not _sel.select([fd], [], [], 0)[0]:
                            break
                        arr = os.read(fd, 1)
            return "cancel"
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def read_key_windows(
    *,
    also_cancel: tuple[bytes, ...] = (),
    space_confirms: bool = True,
    alpha_keys: bool = False,
) -> str:
    """Read one logical keypress on Windows; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"shift_tab"``, ``"right"``, ``"left"``,
    ``"1"``–``"9"``, ``"eof"``, ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"``.
    """
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch in (b"\x03", b"\x1b") or ch in also_cancel:
        return "cancel"
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
        return "cancel"
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

    Returns ``"enter"``, ``"cancel"``, ``"backspace"``, ``"eof"``, or a single
    printable character (ASCII). Multi-byte UTF-8 is ignored for now so the
    Ask User custom row stays a simple in-place field.
    """
    if os.name == "nt":
        return _read_typing_key_windows()
    return _read_typing_key_unix()


def read_menu_or_char(*, allow_chars: bool = False, alpha_keys: bool = False) -> str:
    """Menu navigation keys, optionally plus printable chars / backspace.

    When ``allow_chars`` is True (custom option row focused), typing inserts
    on that row in place; arrows/tab still move between options. When
    ``alpha_keys`` is True (and not typing), an option is selected by its
    ``(A)``/``(B)`` letter instead of a digit.
    """
    if os.name == "nt":
        return _read_menu_or_char_windows(allow_chars=allow_chars, alpha_keys=alpha_keys)
    return _read_menu_or_char_unix(allow_chars=allow_chars, alpha_keys=alpha_keys)


def _read_menu_or_char_unix(*, allow_chars: bool, alpha_keys: bool = False) -> str:
    import select as _sel
    import termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        _raw_input_mode(fd)
        ch = os.read(fd, 1)
        if not ch:
            return "eof"
        b = ch[0]
        if b in (3, 4):
            return "cancel"
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
            return "cancel"
        if b == 27:
            if _sel.select([fd], [], [], 0.1)[0]:
                nxt = os.read(fd, 1)
                if nxt == b"[" and _sel.select([fd], [], [], 0.1)[0]:
                    arr = os.read(fd, 1)
                    if arr == b"A":
                        return "up"
                    if arr == b"B":
                        return "down"
                    if arr == b"C":
                        return "right"
                    if arr == b"D":
                        return "left"
                    if arr == b"Z":
                        return "shift_tab"
                    while arr and not (0x40 <= arr[0] <= 0x7E):
                        if not _sel.select([fd], [], [], 0)[0]:
                            break
                        arr = os.read(fd, 1)
                    return "ignore"
            return "cancel"
        # Space: printable when typing; otherwise a distinct toggle key for multi-select.
        if b == 32:
            return " "
        if allow_chars and 32 <= b <= 126:
            return chr(b)
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def _read_menu_or_char_windows(*, allow_chars: bool, alpha_keys: bool = False) -> str:
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch in (b"\x03",):
        return "cancel"
    if ch in (b"\r", b"\n"):
        return "enter"
    if allow_chars and ch in (b"\x08", b"\x7f"):
        return "backspace"
    if ch == b"\t":
        return "tab"
    if ch == b"\x1b":
        return "cancel"
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
        return "cancel"
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
    import select as _sel
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
        if b == 27:
            # Esc alone cancels; drain CSI so arrows don't leak as chars.
            if _sel.select([fd], [], [], 0.05)[0]:
                nxt = os.read(fd, 1)
                if nxt == b"[":
                    while _sel.select([fd], [], [], 0)[0]:
                        arr = os.read(fd, 1)
                        if arr and 0x40 <= arr[0] <= 0x7E:
                            break
            return "cancel"
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
    "flush_pending_input",
    "flush_stdin_unix",
    "read_key_unix",
    "read_key_windows",
    "read_menu_or_char",
    "read_typing_key",
    "restore_stdin_terminal",
]
