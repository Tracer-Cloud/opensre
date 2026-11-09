"""Tests for CPR stdin hygiene helpers."""

from __future__ import annotations

import pytest

from surfaces.shared.terminal.components.cpr_stdin import (
    contains_cpr_sequence,
    strip_cpr_sequences,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[32;1R", ""),
        ("[32;1R", ""),
        ("\x9b32;1R", ""),
        ("what is our current model?[32;1R", "what is our current model?"),
        ("before \x1b[12;80R after", "before  after"),
        ("7R[25;57R23;57R", ""),
        ("25;57R", ""),
    ],
)
def test_strip_cpr_sequences_removes_terminal_cursor_replies(
    text: str,
    expected: str,
) -> None:
    assert strip_cpr_sequences(text) == expected


def test_contains_cpr_sequence_detects_leaked_bytes() -> None:
    assert contains_cpr_sequence("\x1b[12;80R")
    assert not contains_cpr_sequence("plain prompt text")
