"""Tests for refusing git commands that would change a merge in progress behind the tool's back."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.interactive_shell.shell.merge_guard import git_refusal_during_merge


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_stopped_merge(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "f.txt").write_text("base\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "f.txt").write_text("feature\n")
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    (work / "f.txt").write_text("main\n")
    _git(work, "commit", "-am", "main")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    return work


def test_git_commands_that_alter_a_merge_in_progress_are_refused(tmp_path: Path) -> None:
    # Arrange
    work = _repo_with_stopped_merge(tmp_path)

    # Act
    commit = git_refusal_during_merge("git commit -am 'resolve'", str(work))
    push_via_c = git_refusal_during_merge(f"git -C {work} push origin HEAD:refs/heads/x")
    chained = git_refusal_during_merge("git add . && git checkout main", str(work))
    status = git_refusal_during_merge("git status --short", str(work))

    # Assert
    assert commit is not None and "resolve_merge_conflicts" in commit
    assert push_via_c is not None and "git push" in push_via_c
    assert chained is not None and "git checkout" in chained
    assert status is None


def test_nothing_is_refused_without_a_merge_in_progress(tmp_path: Path) -> None:
    # Arrange
    work = _repo_with_stopped_merge(tmp_path)
    _git(work, "merge", "--abort")

    # Act / Assert
    assert git_refusal_during_merge("git commit -am 'x'", str(work)) is None
    assert git_refusal_during_merge("git push", str(tmp_path / "not-a-repo")) is None
