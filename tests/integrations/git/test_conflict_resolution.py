"""Tests for finishing a stopped merge after a resolver edited the files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from integrations.git import (
    conclude_merge,
    file_fingerprints,
    merge_committed_by_resolver,
    merge_conflicts,
    merge_head_name,
    merge_head_sha,
    merge_in_progress,
    take_side,
    unresolved_conflicts,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _stopped_merge(tmp_path: Path) -> Path:
    """Work tree on ``feature`` with ``main`` half-merged: ``shared.txt`` conflicts."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "shared.txt").write_text("base\n")
    (work / "wip.txt").write_text("wip\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "shared.txt").write_text("feature\n")
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    (work / "shared.txt").write_text("main\n")
    _git(work, "commit", "-am", "main")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge_in_progress(str(work))
    return work


def test_merge_head_name_is_the_branch_being_merged(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)

    # Act
    name = merge_head_name(str(work))

    # Assert
    assert name == "main"


def test_untouched_and_marked_files_stay_unresolved_until_edited(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")

    # Act
    before_edit = unresolved_conflicts(str(work), conflicts)
    (work / "shared.txt").write_text("feature and main\n")
    after_edit = unresolved_conflicts(str(work), conflicts)

    # Assert
    assert [c.path for c in before_edit] == ["shared.txt"]
    assert after_edit == []


def test_conclude_merge_commits_resolver_edits_but_not_baseline_work(tmp_path: Path) -> None:
    # Arrange: a person's edit to wip.txt predates the resolver and must stay uncommitted,
    # even though the resolver staged it along with its own files.
    work = _stopped_merge(tmp_path)
    (work / "wip.txt").write_text("person's work\n")
    baseline = file_fingerprints(str(work), ["wip.txt"])
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "shared.txt").write_text("feature and main\n")
    (work / "added.txt").write_text("resolver added\n")
    _git(work, "add", "wip.txt", "added.txt")

    # Act
    sha = conclude_merge(str(work), conflicts, baseline=baseline)

    # Assert
    assert sha == _git(work, "rev-parse", "HEAD")
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert _git(work, "log", "-1", "--pretty=%an <%ae>|%cn <%ce>") == (
        "OpenSRE Agent <opensreagent@opensre.com>|OpenSRE Agent <opensreagent@opensre.com>"
    )
    assert _git(work, "show", "--name-only", "--pretty=", "HEAD").split() == [
        "added.txt",
        "shared.txt",
    ]
    assert _git(work, "status", "--porcelain").split() == ["M", "wip.txt"]


def _stopped_modify_delete_merge(tmp_path: Path) -> Path:
    """Work tree on ``feature`` where ``main`` deleted ``doomed.txt`` that feature changed."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "doomed.txt").write_text("keep?\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "doomed.txt").write_text("keep, changed on feature\n")
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    _git(work, "rm", "-q", "doomed.txt")
    _git(work, "commit", "-m", "main deletes")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge_in_progress(str(work))
    return work


def test_keeping_a_file_main_deleted_counts_as_resolved_once_staged(tmp_path: Path) -> None:
    # Arrange: the kept file's content does not change, so only the index can tell.
    work = _stopped_modify_delete_merge(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")

    # Act
    untouched = unresolved_conflicts(str(work), conflicts)
    _git(work, "add", "doomed.txt")
    kept = unresolved_conflicts(str(work), conflicts)

    # Assert
    assert [c.path for c in untouched] == ["doomed.txt"]
    assert kept == []


def test_merge_committed_by_resolver_requires_a_merge_of_both_tips_on_the_same_branch(
    tmp_path: Path,
) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    merged = merge_head_sha(str(work))

    # Act: first abandon the merge and check out the incoming branch, then do it right.
    _git(work, "merge", "--abort")
    _git(work, "checkout", "-q", "main")
    abandoned = merge_committed_by_resolver(str(work), conflicts, merged)
    _git(work, "checkout", "-q", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    (work / "shared.txt").write_text("feature and main\n")
    _git(work, "add", "shared.txt")
    _git(work, "commit", "-q", "-m", "merge main")
    committed = merge_committed_by_resolver(str(work), conflicts, merged)

    # Assert
    assert abandoned is False
    assert committed is True


def test_take_side_resolves_a_file_by_one_side_and_a_deleted_side_by_removing_it(
    tmp_path: Path,
) -> None:
    # Arrange: shared.txt conflicts on content; doomed.txt was deleted on main.
    work = _stopped_modify_delete_merge(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")

    # Act
    take_side(str(work), "doomed.txt", "theirs")

    # Assert
    assert unresolved_conflicts(str(work), conflicts) == []
    assert not (work / "doomed.txt").exists()
    assert _git(work, "diff", "--name-only", "--diff-filter=U") == ""


def test_a_content_conflict_whose_file_was_removed_is_not_treated_as_resolved(
    tmp_path: Path,
) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")

    # Act
    (work / "shared.txt").unlink()
    removed = unresolved_conflicts(str(work), conflicts)

    # Assert
    assert [c.path for c in removed] == ["shared.txt"]
