"""The live spinner is braille in every terminal."""

from __future__ import annotations

import pytest

from infrastructure.terminal.spinner_frames import BRAILLE_SPINNER_FRAMES, spinner_frames


@pytest.mark.parametrize("term_program", ["Apple_Terminal", "iTerm.app", "vscode", ""])
def test_the_spinner_is_braille_in_every_terminal(
    monkeypatch: pytest.MonkeyPatch, term_program: str
) -> None:
    # Arrange
    monkeypatch.setenv("TERM_PROGRAM", term_program)

    # Act
    frames = spinner_frames()

    # Assert: never the dot and circle set that rendered as an oversized circle.
    assert frames == BRAILLE_SPINNER_FRAMES
    assert "●" not in frames


def test_spinner_state_uses_the_frames_it_is_given() -> None:
    from surfaces.interactive_shell.runtime.core.state import SpinnerState

    spinner = SpinnerState(frames=("-", "+"))
    assert spinner._SPINNER_FRAMES == ("-", "+")
    assert SpinnerState()._SPINNER_FRAMES == BRAILLE_SPINNER_FRAMES
