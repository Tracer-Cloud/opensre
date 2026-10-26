"""Model-supplied text cannot inject terminal escapes into raw output."""

from __future__ import annotations

from infrastructure.safety.terminal_output import strip_terminal_controls


def test_strips_ansi_escape_and_control_bytes() -> None:
    # Arrange: a plan step carrying a screen-clear escape and a bell.
    hostile = "rm the cache\x1b[2J\x1b[1;1H\x07"

    # Act
    cleaned = strip_terminal_controls(hostile)

    # Assert: the ESC, CSI bytes, and BEL are gone; the words survive.
    assert cleaned == "rm the cache[2J[1;1H"
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned


def test_strips_newlines_tabs_and_del_that_would_corrupt_row_accounting() -> None:
    assert strip_terminal_controls("line1\nline2\tcol\x7f") == "line1line2col"


def test_keep_whitespace_preserves_lf_and_tab_but_still_strips_esc() -> None:
    hostile = "### Phase\nline\tkeep\x1b[2J\x07"
    cleaned = strip_terminal_controls(hostile, keep_whitespace=True)
    assert cleaned == "### Phase\nline\tkeep[2J"
    assert "\x1b" not in cleaned
    assert "\x07" not in cleaned


def test_preserves_printable_unicode() -> None:
    # Non-control Unicode (glyphs, accents, CJK) must pass through untouched.
    assert strip_terminal_controls("café ✓ 日本語 ●○") == "café ✓ 日本語 ●○"
