"""Tests for finishing a stopped merge after a resolver edited the files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from integrations.git import (
    conclude_merge,
    file_fingerprints,
    merge_conflicts,
    merge_head_name,
    merge_in_progress,
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
    # Arrange: a person's edit to wip.txt predates the resolver and must stay uncommitted.
    work = _stopped_merge(tmp_path)
    (work / "wip.txt").write_text("person's work\n")
    baseline = file_fingerprints(str(work), ["wip.txt"])
    conflicts = merge_conflicts(str(work), ours="feature", theirs="main")
    (work / "shared.txt").write_text("feature and main\n")
    (work / "added.txt").write_text("resolver added\n")

    # Act
    sha = conclude_merge(str(work), conflicts, baseline=baseline)

    # Assert
    assert sha == _git(work, "rev-parse", "HEAD")
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert _git(work, "show", "--name-only", "--pretty=", "HEAD").split() == [
        "added.txt",
        "shared.txt",
    ]
    assert _git(work, "status", "--porcelain").split() == ["M", "wip.txt"]
