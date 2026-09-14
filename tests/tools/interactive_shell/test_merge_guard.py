"""Tests for refusing git commands that would change a merge in progress behind the tool's back."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.interactive_shell.shell.merge_guard import git_refusal_during_merge


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_with_stopped_merge(tmp_path: Path, *, dirname: str = "work") -> Path:
    work = tmp_path / dirname
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
    chained = git_refusal_during_merge("git status && git checkout main", str(work))
    status = git_refusal_during_merge("git status --short", str(work))

    # Assert
    assert commit is not None and "resolve_merge_conflicts" in commit
    assert push_via_c is not None and "git push" in push_via_c
    assert chained is not None and "git checkout" in chained
    assert status is None


def test_wrapped_quoted_and_omitted_git_mutations_are_refused(tmp_path: Path) -> None:
    # Arrange: the same stopped merge, also reachable through a path that contains spaces.
    work = _repo_with_stopped_merge(tmp_path)
    spaced = _repo_with_stopped_merge(tmp_path, dirname="path with spaces")

    # Act
    via_env = git_refusal_during_merge("env git commit -am 'resolve'", str(work))
    via_sh = git_refusal_during_merge("sh -c 'git commit -am resolve'", str(work))
    via_quoted_c = git_refusal_during_merge(f"git -C '{spaced}' commit -am x")
    stash = git_refusal_during_merge("git stash", str(work))
    cherry = git_refusal_during_merge("git cherry-pick abcdef", str(work))
    revert = git_refusal_during_merge("git revert HEAD", str(work))

    # Assert
    assert via_env is not None and "git commit" in via_env
    assert via_sh is not None and "git commit" in via_sh
    assert via_quoted_c is not None and "git commit" in via_quoted_c
    assert stash is not None and "git stash" in stash
    assert cherry is not None and "git cherry-pick" in cherry
    assert revert is not None and "git revert" in revert


def test_nothing_is_refused_without_a_merge_in_progress(tmp_path: Path) -> None:
    # Arrange
    work = _repo_with_stopped_merge(tmp_path)
    _git(work, "merge", "--abort")

    # Act / Assert
    assert git_refusal_during_merge("git commit -am 'x'", str(work)) is None
    assert git_refusal_during_merge("git push", str(tmp_path / "not-a-repo")) is None
