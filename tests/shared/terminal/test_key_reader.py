"""Tests for terminal cooked-mode restore after raw-mode menus."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from surfaces.shared.terminal.components import key_reader


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
