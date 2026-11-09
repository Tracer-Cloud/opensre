"""Sound-notification gating and playback fallbacks."""

from __future__ import annotations

import io
import sys

import pytest

from config.constants import SOUND_NOTIFICATIONS_ENV
from infrastructure.terminal import notify
from infrastructure.terminal.notify import (
    NotifyEvent,
    play_notification,
    sound_enabled,
    terminal_is_focused,
)


def test_sound_is_disabled_unless_env_is_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SOUND_NOTIFICATIONS_ENV, raising=False)
    assert not sound_enabled()
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")
    assert sound_enabled()
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "false")
    assert not sound_enabled()


def test_play_is_a_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disabled: no bell, no subprocess — the shell stays silent by default.
    monkeypatch.delenv(SOUND_NOTIFICATIONS_ENV, raising=False)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == ""


def test_non_darwin_falls_back_to_the_terminal_bell(monkeypatch: pytest.MonkeyPatch) -> None:
    # When enabled off macOS, focus is undeterminable so it chimes (bell fallback).
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.INPUT_NEEDED)
    assert buf.getvalue() == "\a"


def test_stays_silent_when_the_terminal_is_focused(monkeypatch: pytest.MonkeyPatch) -> None:
    # No chime while you are already looking at the terminal.
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")
    monkeypatch.setattr(notify, "terminal_is_focused", lambda: True)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == ""


def test_chimes_when_the_terminal_is_unfocused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")
    monkeypatch.setattr(notify, "terminal_is_focused", lambda: False)
    monkeypatch.setattr(notify.platform, "system", lambda: "Linux")
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert buf.getvalue() == "\a"


def test_focus_is_false_only_when_a_different_app_is_frontmost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # App-name match cannot prove *this* window is focused (another Code/iTerm
    # window may be), so same-host is undeterminable. A different app is not us.
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Code")
    assert terminal_is_focused() is None
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Safari")
    assert terminal_is_focused() is False
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: None)
    assert terminal_is_focused() is None


def test_same_host_app_does_not_suppress_the_chime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Switching to another window of the same terminal must still chime: app
    # matching cannot prove this OpenSRE window is the one in front.
    played: list[NotifyEvent] = []
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Code")
    monkeypatch.setattr(notify, "_play", played.append)
    play_notification(NotifyEvent.TURN_COMPLETE)
    assert played == [NotifyEvent.TURN_COMPLETE]


def test_unknown_term_program_does_not_treat_any_terminal_as_focused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without TERM_PROGRAM we cannot name our host, so a frontmost iTerm/Code/…
    # must not suppress the opted-in chime (degrade to undeterminable).
    monkeypatch.setattr(notify.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "iTerm2")
    assert terminal_is_focused() is None
    monkeypatch.setattr(notify, "_macos_frontmost_app", lambda: "Safari")
    assert terminal_is_focused() is None


def test_playback_errors_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    # A notification must never disrupt the turn — a failing player is swallowed.
    monkeypatch.setenv(SOUND_NOTIFICATIONS_ENV, "1")

    def _boom() -> str:
        raise RuntimeError("no audio device")

    monkeypatch.setattr(notify.platform, "system", _boom)
    play_notification(NotifyEvent.TURN_COMPLETE)  # must not raise
