"""Tests for the local-git merge helpers against a real temp repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from integrations.git import (
    MERGE_FAILED,
    GitCommandError,
    abort_merge,
    commit_merge,
    describe_conflicts,
    fetch_remote_branch,
    head_sha,
    is_ancestor,
    merge_in_progress,
    merge_ref,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _diverged_repo(tmp_path: Path) -> Path:
    """Work tree on ``feature`` whose ``origin/main`` changed the same line and deleted a file."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "shared.txt").write_text("base\n")
    (work / "doomed.txt").write_text("keep?\n")
    (work / "untouched.txt").write_text("same\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")

    _git(work, "checkout", "-b", "feature")
    (work / "shared.txt").write_text("feature\n")
    (work / "doomed.txt").write_text("feature edit\n")
    _git(work, "commit", "-am", "feature change")

    _git(work, "checkout", "main")
    (work / "shared.txt").write_text("main\n")
    _git(work, "rm", "-q", "doomed.txt")
    (work / "only-main.txt").write_text("new on main\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "main change")
    _git(work, "push", "origin", "main")
    _git(work, "reset", "--hard", "HEAD~1")
    _git(work, "checkout", "feature")
    return work


def test_merge_ref_stops_on_conflicts_and_describes_each_side(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")

    # Act
    merged = merge_ref(str(work), "origin/main", message="Merge main")

    # Assert
    assert merged is False
    assert merge_in_progress(str(work)) is True
    assert sorted(unmerged_paths(str(work))) == ["doomed.txt", "shared.txt"]
    described = {
        c.path: c.description for c in describe_conflicts(str(work), ours="feature", theirs="main")
    }
    assert described == {
        "shared.txt": "changed on both feature and main",
        "doomed.txt": "changed on feature, deleted on main",
    }
    assert paths_with_conflict_markers(str(work), ["shared.txt", "doomed.txt"]) == ["shared.txt"]


def test_resolved_merge_commits_with_both_parents(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main into feature")
    (work / "shared.txt").write_text("feature+main\n")
    (work / "doomed.txt").unlink()

    # Act
    stage_paths(str(work), ["shared.txt", "doomed.txt"])
    sha = commit_merge(str(work))

    # Assert
    assert unmerged_paths(str(work)) == []
    assert merge_in_progress(str(work)) is False
    assert sha == head_sha(str(work))
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert is_ancestor(str(work), "origin/main", "HEAD") is True
    message = _git(work, "log", "-1", "--pretty=%B")
    assert "Merge main into feature" in message
    assert "# Conflicts:" not in message
    assert not (work / "doomed.txt").exists()
    assert (work / "only-main.txt").read_text() == "new on main\n"


def test_stage_paths_accepts_a_deletion_the_resolver_already_staged(tmp_path: Path) -> None:
    # Arrange: the resolver ran ``git rm`` itself, so the path is in neither index nor tree.
    work = _diverged_repo(tmp_path)
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main")
    (work / "shared.txt").write_text("feature+main\n")
    _git(work, "rm", "-q", "doomed.txt")

    # Act
    stage_paths(str(work), ["shared.txt", "doomed.txt"])
    commit_merge(str(work))

    # Assert
    assert unmerged_paths(str(work)) == []
    assert not (work / "doomed.txt").exists()
    assert "doomed.txt" not in _git(work, "ls-files")


def test_abort_merge_restores_pre_merge_head(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)
    before = head_sha(str(work))
    fetch_remote_branch(str(work), "main")
    merge_ref(str(work), "origin/main", message="Merge main")

    # Act
    abort_merge(str(work))

    # Assert
    assert merge_in_progress(str(work)) is False
    assert head_sha(str(work)) == before
    assert (work / "shared.txt").read_text() == "feature\n"


def test_merge_ref_raises_on_non_conflict_failure(tmp_path: Path) -> None:
    # Arrange
    work = _diverged_repo(tmp_path)

    # Act / Assert: the ref was never fetched, so git fails without any conflict.
    with pytest.raises(GitCommandError) as excinfo:
        merge_ref(str(work), "origin/does-not-exist", message="Merge")
    assert excinfo.value.kind == MERGE_FAILED
    assert merge_in_progress(str(work)) is False
