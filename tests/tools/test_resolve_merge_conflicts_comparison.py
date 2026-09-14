"""Tests for the side-by-side hunk view the merge tool paints in the shell."""

from __future__ import annotations

import io

from rich.console import Console

from integrations.git import HunkComparison
from tools.cross_vendor.resolve_merge_conflicts.comparison import (
    comparison_text,
    render_comparison,
)

_COMPARISONS = [
    HunkComparison("app.py", ("limit = 250",), ("limit = 50",), ("limit = 250",)),
    HunkComparison("app.py", ("retries = 5",), ("retries = 1",), None),
    HunkComparison("lock.json", ("v1",), ("v2",), ("v2",)),
]


def test_render_comparison_draws_one_table_per_file_with_ours_theirs_and_result() -> None:
    # Arrange
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)

    # Act
    render_comparison(console, _COMPARISONS, ours="feature", theirs="main")
    text = buffer.getvalue()

    # Assert
    assert "app.py · 2 conflicts" in text
    assert "lock.json · 1 conflict" in text
    assert "feature (ours)" in text and "main (theirs)" in text and "merged result" in text
    assert "limit = 250" in text and "limit = 50" in text
    assert "(still conflicted)" in text


def test_comparison_text_lists_each_hunk_for_surfaces_without_a_console() -> None:
    # Arrange / Act
    text = comparison_text(_COMPARISONS, ours="feature", theirs="main")

    # Assert
    assert "app.py conflict 1" in text and "app.py conflict 2" in text
    assert (
        "  feature:\n    retries = 5\n  main:\n    retries = 1\n  merged:\n    (still conflicted)"
        in text
    )
