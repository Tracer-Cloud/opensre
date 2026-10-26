"""Single-keypress confirmation reader for turns where the arrow-nav prompt app
is unavailable (a subprocess owns the TTY).

Reading in cbreak mode disables terminal echo, so arrow keys and other escape
sequences no longer leak as raw ``^[[A`` garbage — they are swallowed and the
loop waits for a real choice (a row tag, a 1-based digit, the row's answer key,
or Enter for the default). Falls back to a cooked ``input()`` line where no
interactive TTY is available (non-TTY, or a platform without ``termios``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

#: Row shape shared with the confirmation gate: ``(answer_key, label)`` pairs.
ConfirmRows = tuple[tuple[str, str], ...]

#: How long to wait for the CSI introducer after ESC before treating it as
#: a standalone Escape (no tail bytes will arrive).
_ESCAPE_INTRODUCER_WAIT_S = 0.05

#: SS3 finals for application-mode arrows, Home/End, and F1–F4 (``ESC O …``).
#: Uppercase only — a following ``a``/``b`` is a confirmation choice, not an arrow.
_SS3_KEYBOARD_FINALS = frozenset("ABCDHFPRS")


def resolve_confirm_answer(key: str, rows: ConfirmRows) -> str | None:
    """Map one confirmation keypress to a row answer, or ``None`` to keep waiting.

    Accepts a row tag (``a``/``b``/…), a 1-based digit, or the row's own answer
    key; an empty key (Enter) takes the arrow-nav default — the last row, which
    is always cancel. Any other key returns ``None`` so the caller ignores it.
    """
    normalized = key.strip().lower()
    if not normalized:
        return rows[-1][0]
    for index, (answer, _label) in enumerate(rows):
        if normalized in {chr(ord("a") + index), str(index + 1), answer}:
            return answer
    return None


def read_confirm_answer(prompt: str, rows: ConfirmRows) -> str:
    """Print the rows, read one confirmation choice, and return its answer key."""
    for index, (_answer, label) in enumerate(rows):
        print(f"  [{chr(ord('a') + index)}] {label}")
    tags = "/".join(chr(ord("a") + index) for index in range(len(rows)))
    cancel = rows[-1][0]
    if not _stdin_is_interactive_tty():
        return _read_line_answer(prompt, rows, tags, cancel)
    try:
        return _read_key_answer(prompt, rows, tags, cancel)
    except (ImportError, OSError):
        # No termios (e.g. Windows) or the TTY rejected raw mode.
        return _read_line_answer(prompt, rows, tags, cancel)


def _stdin_is_interactive_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def _stdin_has_pending(fd: int, timeout: float) -> bool:
    """True when *fd* has a byte ready within *timeout* seconds."""
    import select

    try:
        return bool(select.select([fd], [], [], timeout)[0])
    except (OSError, ValueError):
        return False


def _is_csi_parameter_or_intermediate(char: str) -> bool:
    """True for CSI parameter (``0–9:;<=>?``) or intermediate (space–``/``) bytes."""
    return len(char) == 1 and 0x20 <= ord(char) <= 0x3F


def _is_keyboard_csi_final(char: str) -> bool:
    """True for CSI finals used by arrows, Home/End, Delete, and CPR.

    The full ECMA-48 final range is 0x40–0x7E, which includes ``a``/``b``/``y``/``n``.
    Those are confirmation choices here, not navigation, so they must not be
    swallowed as a CSI terminator.
    """
    if len(char) != 1:
        return False
    code = ord(char)
    return 0x40 <= code <= 0x5F or code == 0x7E


def _drain_escape_tail(
    read_char: Callable[[], str],
    *,
    has_input: Callable[[float], bool],
    first_wait: float = _ESCAPE_INTRODUCER_WAIT_S,
) -> str:
    """Swallow a CSI or SS3 tail after ESC. Return any leftover non-nav byte.

    Arrow keys arrive as ``ESC [ A`` (CSI) or ``ESC O A`` (SS3, application
    cursor mode). A lone Escape has no tail — *has_input* must return False so
    this returns immediately and does not consume the next intended choice.

    An incomplete ``ESC [`` / ``ESC O`` followed by a choice letter
    (``a``/``b``/``y``/``n``) returns that letter so the confirmation reader
    can still resolve it. A complete SS3 arrow is consumed in full: returning
    only ``O`` would leave ``A``/``B`` for the next read, and the resolver
    case-folds those onto rows ``a``/``b`` (Yes / always allow).
    """
    if not has_input(first_wait):
        return ""
    introducer = read_char()
    if introducer == "O":
        if not has_input(0):
            return ""
        nxt = read_char()
        if nxt in _SS3_KEYBOARD_FINALS:
            return ""
        return nxt
    if introducer != "[":
        return introducer
    while has_input(0):
        nxt = read_char()
        if not nxt:
            break
        if _is_csi_parameter_or_intermediate(nxt):
            continue
        if _is_keyboard_csi_final(nxt):
            break
        return nxt
    return ""


def _read_key_answer(prompt: str, rows: ConfirmRows, tags: str, cancel: str) -> str:
    """Read one keypress in cbreak mode (echo off); swallow arrow/escape keys."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    sys.stdout.write(f"{prompt} [{tags}] ")
    sys.stdout.flush()
    answer = cancel
    try:
        tty.setcbreak(fd)
        while True:
            try:
                char = sys.stdin.read(1)
            except KeyboardInterrupt:
                break
            if not char:
                break
            if char == "\x1b":
                # Arrow/nav escape (ESC [ A or ESC O A): drain the CSI/SS3
                # tail, ignore. A standalone Escape has no tail — do not block.
                leftover = _drain_escape_tail(
                    lambda: sys.stdin.read(1),
                    has_input=lambda timeout: _stdin_has_pending(fd, timeout),
                )
                if leftover:
                    resolved = resolve_confirm_answer(leftover, rows)
                    if resolved is not None:
                        answer = resolved
                        break
                continue
            if char in ("\r", "\n"):
                break
            resolved = resolve_confirm_answer(char, rows)
            if resolved is not None:
                answer = resolved
                break
            # Unknown printable key: ignore and keep waiting.
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    sys.stdout.write(f"{answer}\n")
    sys.stdout.flush()
    return answer


def _read_line_answer(prompt: str, rows: ConfirmRows, tags: str, cancel: str) -> str:
    """Cooked one-line read for non-TTY / no-termios environments."""
    try:
        raw = input(f"{prompt} [{tags}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return cancel
    resolved = resolve_confirm_answer(raw, rows)
    return resolved if resolved is not None else raw


__all__ = ["ConfirmRows", "read_confirm_answer", "resolve_confirm_answer"]
