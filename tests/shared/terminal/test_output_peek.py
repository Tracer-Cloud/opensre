"""Collapsed tool-output peek + expand marker."""

from __future__ import annotations

from infrastructure.terminal.peek import (
    build_output_peek,
    cap_output_for_display,
    format_expand_marker,
    format_view_all_marker,
)


def test_short_output_is_returned_whole_with_nothing_hidden() -> None:
    peek, hidden = build_output_peek("one\ntwo", max_lines=3)
    assert peek == "one\ntwo"
    assert hidden == 0


def test_long_output_is_cut_to_the_head_and_reports_the_remainder() -> None:
    full = "\n".join(f"line {i}" for i in range(10))
    peek, hidden = build_output_peek(full, max_lines=3)
    assert peek == "line 0\nline 1\nline 2"
    assert hidden == 7


def test_empty_output_hides_nothing() -> None:
    assert build_output_peek("   \n  ") == ("", 0)


def test_expand_marker_matches_droid_copy() -> None:
    assert format_expand_marker(7) == "… 7 more, Ctrl+O to view"
    assert format_expand_marker(1) == "… 1 more, Ctrl+O to view"
    assert format_view_all_marker() == "Ctrl+O to view all"


def test_cap_output_uses_one_marker_when_both_caps_apply() -> None:
    # Exceed both the line cap (12) and the char cap (800): the hidden-line
    # expand marker wins, and the peek itself still ends with the inline ``…``.
    line = "y" * 130
    text = "\n".join([line] * 20)
    preview, folded = cap_output_for_display(text)
    assert folded == text
    assert preview.count("Ctrl+O to view") == 1
    assert "output truncated" not in preview
    assert preview.rstrip().endswith("Ctrl+O to view")
    body, _, _ = preview.rpartition("\n")
    assert body.endswith("…")


def _boxed_table(*, data_rows: int) -> str:
    return "\n".join(
        [
            "┌────────┬────────┐",
            "│ ID     │ Name   │",
            "├────────┼────────┤",
            *(f"│ id{i:<5}│ name{i:<3}│" for i in range(data_rows)),
            "└────────┴────────┘",
        ]
    )


def test_boxed_table_is_not_folded_mid_box_or_capped_mid_line() -> None:
    # A rendered table (box-drawing chars) must show whole rows, never a partial
    # box or a mid-line character cut.
    text = _boxed_table(data_rows=8)
    preview, folded = cap_output_for_display(text)
    assert folded is None  # shown whole, nothing stashed
    assert preview == text
    assert "Ctrl+O" not in preview
    assert "…" not in preview


def test_long_boxed_table_is_shown_whole_including_the_closing_border() -> None:
    # A table over the old 40-line fold still has to stay a complete box —
    # chopping it replaces the closing border with the expand marker.
    text = _boxed_table(data_rows=50)
    assert text.count("\n") + 1 > 40
    preview, folded = cap_output_for_display(text)
    assert folded is None
    assert preview == text
    assert preview.endswith("└────────┴────────┘")
    assert "Ctrl+O" not in preview
    assert "…" not in preview
