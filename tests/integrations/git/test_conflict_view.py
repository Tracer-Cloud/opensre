"""Tests for the side-by-side hunk view the merge tool paints in the shell."""

from __future__ import annotations

import io

from rich.console import Console

from integrations.git import (
    HunkComparison,
    comparison_text,
    render_comparison,
    render_overview,
    render_review,
    verdict,
)

_COMPARISONS = [
    HunkComparison("app.py", ("limit = 250",), ("limit = 50",), ("limit = 250",)),
    HunkComparison("app.py", ("retries = 5",), ("retries = 1",), None),
    HunkComparison("lock.json", ("v1",), ("v2",), ("v2",)),
]


def test_render_comparison_stacks_ours_theirs_and_result_as_full_width_code() -> None:
    # Arrange
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)

    # Act
    render_comparison(console, _COMPARISONS, ours="feature", theirs="main")
    text = buffer.getvalue()

    # Assert: one block per hunk, both sides and the result readable as whole lines.
    assert "app.py · conflict 1 of 2" in text and "app.py · conflict 2 of 2" in text
    assert "lock.json · conflict 1 of 1" in text
    assert "ours · feature" in text and "theirs · main" in text and "merged" in text
    assert "limit = 250" in text and "limit = 50" in text
    assert "(still conflicted)" in text


def test_long_lines_are_cropped_not_folded_and_common_indentation_is_dropped() -> None:
    # Arrange: a deeply indented, very long line must stay on one row.
    long_line = "        " + "x = " + "a" * 300
    buffer = io.StringIO()
    console = Console(file=buffer, width=80, force_terminal=False, color_system=None)

    # Act
    render_comparison(
        console,
        [HunkComparison("app.py", (long_line,), ("        y = 1",), None)],
        ours="feature",
        theirs="main",
    )
    rows = [row for row in buffer.getvalue().splitlines() if "x = " in row or "y = 1" in row]

    # Assert
    assert len(rows) == 2
    assert rows[1].strip() == "y = 1"


def test_comparison_text_lists_each_hunk_for_surfaces_without_a_console() -> None:
    # Arrange / Act
    text = comparison_text(_COMPARISONS, ours="feature", theirs="main")

    # Assert
    assert "app.py conflict 1" in text and "app.py conflict 2" in text
    assert (
        "  feature:\n    retries = 5\n  main:\n    retries = 1\n  merged:\n    (still conflicted)"
        in text
    )


def test_overview_is_one_line_per_conflict_with_a_hint_of_each_side() -> None:
    # Arrange
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)

    # Act
    render_overview(console, _COMPARISONS, ours="feature", theirs="main")
    text = buffer.getvalue()

    # Assert
    assert "Merging main into feature" in text
    assert "app.py · 2 conflicts" in text and "lock.json · 1 conflict" in text
    assert "conflict 1  ours limit = 250  theirs limit = 50" in text
    assert "merged result" not in text and "┃" not in text


def test_review_gives_a_verdict_per_conflict_and_code_only_where_it_was_combined() -> None:
    # Arrange
    combined = HunkComparison("app.py", ("a",), ("b",), ("a", "b"))
    kept = HunkComparison("app.py", ("limit = 250",), ("limit = 50",), ("limit = 250",))
    took = HunkComparison("lock.json", ("v1",), ("v2",), ("v2",))
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)

    # Act
    render_review(console, [combined, kept, took], ours="feature", theirs="main")
    text = buffer.getvalue()

    # Assert
    assert [verdict(h) for h in (combined, kept, took)] == ["combined", "kept ours", "took theirs"]
    assert "conflict 1  combined" in text and "merged" in text
    assert "conflict 2  kept ours (feature)  limit = 250" in text
    assert "conflict 1  took theirs (main)  v2" in text
    assert text.count("merged") == 1


def test_a_removed_file_and_an_emptied_hunk_get_different_verdicts() -> None:
    # Arrange / Act / Assert
    assert (
        verdict(HunkComparison("gone.txt", ("a",), ("b",), (), file_removed=True)) == "file removed"
    )
    assert verdict(HunkComparison("kept.txt", ("a",), ("b",), ())) == "dropped both sides"
