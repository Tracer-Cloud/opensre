"""Tests for reading conflict hunks and pairing them with their resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

from integrations.git import (
    compare_hunks,
    conclude_merge,
    merge_conflicts,
    merge_in_progress,
    parse_conflict_hunks,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


_FILLER = "\n".join(f"line {n}" for n in range(1, 13))


def _stopped_merge_with_two_hunks(tmp_path: Path) -> Path:
    """``feature`` and ``main`` edit the first and last line of ``app.py`` differently."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")

    def body(first: str, last: str) -> str:
        return f"{first}\n{_FILLER}\n{last}\n"

    (work / "app.py").write_text(body("two", "six"))
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "app.py").write_text(body("two from feature", "six from feature"))
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    (work / "app.py").write_text(body("two from main", "six from main"))
    _git(work, "commit", "-am", "main")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge_in_progress(str(work))
    return work


def test_parse_conflict_hunks_reads_both_sides_and_skips_a_diff3_base() -> None:
    # Arrange
    lines = (
        "keep",
        "<<<<<<< HEAD",
        "ours 1",
        "ours 2",
        "||||||| base",
        "base",
        "=======",
        "theirs 1",
        ">>>>>>> main",
        "tail",
    )

    # Act
    hunks = parse_conflict_hunks(lines)

    # Assert
    assert len(hunks) == 1
    assert hunks[0].ours == ("ours 1", "ours 2")
    assert hunks[0].theirs == ("theirs 1",)
    assert (hunks[0].start, hunks[0].end) == (1, 9)


def test_compare_hunks_pairs_each_conflict_with_its_resolved_lines(tmp_path: Path) -> None:
    # Arrange: two hunks; the resolver keeps ours for the first and combines the second.
    work = _stopped_merge_with_two_hunks(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "app.py").write_text(f"two from feature\n{_FILLER}\nsix from feature and main\n")

    # Act
    comparisons = compare_hunks(str(work), conflicts)

    # Assert
    assert [(c.ours, c.theirs) for c in comparisons] == [
        (("two from feature",), ("two from main",)),
        (("six from feature",), ("six from main",)),
    ]
    assert comparisons[0].result == ("two from feature",)
    assert comparisons[1].result == ("six from feature and main",)


def test_compare_hunks_marks_a_file_still_holding_markers_as_unresolved(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge_with_two_hunks(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")

    # Act
    comparisons = compare_hunks(str(work), conflicts)

    # Assert
    assert len(comparisons) == 2
    assert all(c.result is None for c in comparisons)


def test_compare_hunks_can_read_the_committed_resolution_instead_of_the_tree(
    tmp_path: Path,
) -> None:
    # Arrange: resolve, commit, then change the working tree afterwards.
    work = _stopped_merge_with_two_hunks(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "app.py").write_text(f"two from feature\n{_FILLER}\nsix from main\n")
    sha = conclude_merge(str(work), conflicts, baseline={})
    (work / "app.py").write_text("drifted after the commit\n")

    # Act
    committed = compare_hunks(str(work), conflicts, revision=sha)

    # Assert: the report follows the commit, not the tree.
    assert [c.result for c in committed] == [("two from feature",), ("six from main",)]


def test_a_file_removed_by_the_resolution_reads_as_removed_not_conflicted(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge_with_two_hunks(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "app.py").unlink()

    # Act
    comparisons = compare_hunks(str(work), conflicts)

    # Assert: the file is gone; None would mean markers remain.
    assert all(c.result == () and c.file_removed for c in comparisons)


def test_a_file_emptied_by_the_resolution_is_not_reported_as_removed(tmp_path: Path) -> None:
    # Arrange: the resolution keeps the file but drops every line, then commits.
    work = _stopped_merge_with_two_hunks(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "app.py").write_text("")
    sha = conclude_merge(str(work), conflicts, baseline={})

    # Act
    in_tree = compare_hunks(str(work), conflicts)
    committed = compare_hunks(str(work), conflicts, revision=sha)

    # Assert: resolved to nothing, but the file still exists in both views.
    assert all(c.result == () and not c.file_removed for c in in_tree)
    assert all(c.result == () and not c.file_removed for c in committed)
