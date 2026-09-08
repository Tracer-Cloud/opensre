"""Apple Terminal gets one-cell dot frames; every other terminal keeps braille."""

from __future__ import annotations

from infrastructure.terminal.spinner_frames import (
    BRAILLE_SPINNER_FRAMES,
    DOT_SPINNER_FRAMES,
    spinner_frames,
)


def test_apple_terminal_gets_dot_frames_and_others_keep_braille() -> None:
    assert spinner_frames("Apple_Terminal") == DOT_SPINNER_FRAMES
    assert spinner_frames("iTerm.app") == BRAILLE_SPINNER_FRAMES
    assert spinner_frames("") == BRAILLE_SPINNER_FRAMES


def test_spinner_state_uses_the_frames_it_is_given() -> None:
    from surfaces.interactive_shell.runtime.core.state import SpinnerState

    spinner = SpinnerState(frames=DOT_SPINNER_FRAMES)
    assert spinner._SPINNER_FRAMES == DOT_SPINNER_FRAMES
    assert SpinnerState()._SPINNER_FRAMES == BRAILLE_SPINNER_FRAMES
