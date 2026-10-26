"""Raw-mode key reads for inline menus, and the cooked-mode restore after them."""

from __future__ import annotations

import functools
import os
from types import SimpleNamespace

import pytest

from surfaces.shared.terminal.components import key_reader
from tests.shared.terminal.pty_keyboard import pty_stdin


def test_restore_stdin_terminal_recooks_input_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raw menu (tty.setraw) clears ICRNL, so Enter (CR) stops submitting until it
    # is restored. Pin that restore re-enables the cooked-mode flags on a raw snapshot.
    termios = pytest.importorskip("termios")

    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
    )

    raw_attrs = [0, 0, 0, 0, 0, 0, []]  # iflag/oflag/cflag/lflag all cleared (raw mode)
    written: dict[str, list[object]] = {}
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: list(raw_attrs))
    monkeypatch.setattr(
        termios, "tcsetattr", lambda _fd, _when, attrs: written.__setitem__("attrs", attrs)
    )
    monkeypatch.setattr(termios, "tcflush", lambda _fd, _queue: None)

    key_reader.restore_stdin_terminal()

    attrs = written["attrs"]
    assert attrs[0] & termios.ICRNL  # CR -> NL so Enter submits again
    assert attrs[1] & termios.OPOST  # output newline post-processing
    assert attrs[3] & termios.ICANON  # line editing
    assert attrs[3] & termios.ECHO  # keystrokes visible


def test_restore_stdin_terminal_is_a_no_op_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False, fileno=lambda: (_ for _ in ()).throw(AssertionError)),
    )
    key_reader.restore_stdin_terminal()  # returns without touching termios


def test_alpha_option_key_maps_only_ascii_letters() -> None:
    # Arrange / Act / Assert: letters map to their uppercase; anything else is None.
    assert key_reader._alpha_option_key(ord("a")) == "A"
    assert key_reader._alpha_option_key(ord("C")) == "C"
    assert key_reader._alpha_option_key(ord("1")) is None
    assert key_reader._alpha_option_key(ord("-")) is None


def _drive_read_key_unix(monkeypatch: pytest.MonkeyPatch, byte: bytes, *, alpha_keys: bool) -> str:
    """Run read_key_unix once with ``byte`` queued on a faked raw TTY."""
    termios = pytest.importorskip("termios")
    tty = pytest.importorskip("tty")
    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(key_reader.sys, "stdin", SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(key_reader.os, "read", lambda _fd, _n: byte)
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: [0, 0, 0, 0, 0, 0, []])
    monkeypatch.setattr(termios, "tcsetattr", lambda _fd, _when, _attrs: None)
    monkeypatch.setattr(tty, "setraw", lambda _fd: None)
    return key_reader.read_key_unix(alpha_keys=alpha_keys)


def test_read_key_unix_alpha_mode_surfaces_option_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange / Act / Assert: in alpha mode a letter key IS the selection.
    assert _drive_read_key_unix(monkeypatch, b"b", alpha_keys=True) == "B"


def test_read_key_unix_alpha_mode_disables_vim_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    # 'j'/'k' are vim nav only when numeric; as an option letter they must select,
    # otherwise pressing option (J) would scroll instead of choosing it.
    assert _drive_read_key_unix(monkeypatch, b"j", alpha_keys=True) == "J"
    assert _drive_read_key_unix(monkeypatch, b"j", alpha_keys=False) == "down"


def test_read_key_unix_alpha_mode_ignores_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Numbering is off in letter menus, so a digit is inert rather than a selector.
    assert _drive_read_key_unix(monkeypatch, b"2", alpha_keys=True) == "ignore"
    assert _drive_read_key_unix(monkeypatch, b"2", alpha_keys=False) == "2"


def test_raw_key_reads_keep_output_post_processing() -> None:
    """A termios snapshot taken during a key read is restored later; it must not carry bare LFs.

    With OPOST cleared, a report painted mid-turn started every line where the
    previous one ended and walked across the screen.
    """
    termios = pytest.importorskip("termios")
    pty = pytest.importorskip("pty")
    # Arrange: a real terminal, cooked.
    master, slave = pty.openpty()
    try:
        assert termios.tcgetattr(slave)[1] & termios.OPOST

        # Act
        key_reader._raw_input_mode(slave)
        during = termios.tcgetattr(slave)

        # Assert: keystrokes are raw, output is still post-processed.
        assert not during[3] & termios.ICANON
        assert during[1] & termios.OPOST
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    "sequence",
    [
        pytest.param(b"\x1b[I", id="focus-in"),
        pytest.param(b"\x1b[O", id="focus-out"),
        pytest.param(b"\x1b[12;1R", id="cpr-reply"),
        pytest.param(b"\x1b[<0;10;20M", id="sgr-mouse"),
        pytest.param(b"\x1b[H", id="home"),
        pytest.param(b"\x1b[F", id="end"),
        pytest.param(b"\x1b[3~", id="delete"),
        pytest.param(b"\x1b[5~", id="page-up"),
        pytest.param(b"\x1b[?62;4c", id="da1-reply"),
        pytest.param(b"\x1b]11;rgb:1e1e/1e1e/1e1e\x1b\\", id="osc11-reply"),
        pytest.param(b"\x1bb", id="option-b"),
    ],
)
def test_stray_escape_sequences_are_ignored_whole(
    monkeypatch: pytest.MonkeyPatch, sequence: bytes
) -> None:
    """A terminal reply or unmapped key used to read as Esc and close the picker."""
    dismissed: list[str] = []
    readers = (
        functools.partial(key_reader.read_key_unix, alpha_keys=True, on_dismiss=dismissed.append),
        functools.partial(key_reader.read_key_unix, alpha_keys=False, on_dismiss=dismissed.append),
        functools.partial(key_reader.read_menu_or_char, on_dismiss=dismissed.append),
    )
    with pty_stdin(monkeypatch) as keyboard:
        for read in readers:
            keyboard.queue(sequence)

            assert read() == "ignore"
            assert keyboard.unread() == b""  # no tail left to be read as keys

    assert dismissed == []


def test_normal_and_application_cursor_arrows_navigate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Application-cursor mode sends ``ESC O A``; it read as Esc and cancelled.
    arrows = {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}
    with pty_stdin(monkeypatch) as keyboard:
        for introducer in (b"[", b"O"):
            for final, key in arrows.items():
                keyboard.queue(b"\x1b" + introducer + final)

                assert key_reader.read_key_unix() == key


@pytest.mark.parametrize(
    ("keystroke", "dismiss_key"),
    [
        pytest.param(b"\x1b", "esc", id="lone-esc"),
        pytest.param(b"\x1b\x1b", "esc", id="esc-esc"),
        pytest.param(b"\x03", "ctrl_c", id="ctrl-c"),
        pytest.param(b"\x04", "ctrl_d", id="ctrl-d"),
    ],
)
def test_deliberate_cancel_keys_still_dismiss(
    monkeypatch: pytest.MonkeyPatch, keystroke: bytes, dismiss_key: str
) -> None:
    dismissed: list[str] = []
    with pty_stdin(monkeypatch) as keyboard:
        keyboard.queue(keystroke)

        assert key_reader.read_key_unix(on_dismiss=dismissed.append) == "cancel"

    assert dismissed == [dismiss_key]


def test_reply_is_read_through_its_final_byte_and_no_further(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reply cut short left ``;1R`` behind, and its digit selected a numbered
    # option; reading past the final byte would swallow the next real key.
    with pty_stdin(monkeypatch) as keyboard:
        keyboard.queue(b"\x1b[12;1R2")

        assert key_reader.read_key_unix() == "ignore"
        assert keyboard.unread() == b"2"
