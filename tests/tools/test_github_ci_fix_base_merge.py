"""Tests for merging the base branch into a conflicted PR head before the CI fix."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from integrations.coding_agent import CodingResult
from integrations.git import head_sha, merge_in_progress
from integrations.github.tools.ci_fix.base_merge import merge_base_into_head
from integrations.github.tools.ci_fix.context import MERGE_STATE_DIRTY, CiFixContext
from integrations.github.tools.ci_fix.errors import ERR_MERGE_CONFLICT, GitHubCiFixError

_CTX = CiFixContext(
    owner="Tracer-Cloud",
    repo="webapp",
    number=39,
    title="chore: bump deps",
    url="https://github.com/Tracer-Cloud/webapp/pull/39",
    base_branch="main",
    head_branch="ci-fix",
    head_sha="abc123",
    skipped_check_names=(),
    failing_checks=(),
    task="",
    merge_state=MERGE_STATE_DIRTY,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, *, conflict: bool) -> Path:
    """Work tree on ``ci-fix`` with ``origin/main`` ahead; optionally on the same lines."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "package.json").write_text('{"dep": "1.0"}\n')
    (work / "pnpm-lock.yaml").write_text("dep: 1.0\n")
    (work / "README.md").write_text("readme\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")

    _git(work, "checkout", "-b", "ci-fix")
    (work / "package.json").write_text('{"dep": "1.1"}\n')
    (work / "pnpm-lock.yaml").write_text("dep: 1.1\n")
    _git(work, "commit", "-am", "bump dep")

    _git(work, "checkout", "main")
    if conflict:
        (work / "package.json").write_text('{"dep": "2.0"}\n')
        (work / "pnpm-lock.yaml").write_text("dep: 2.0\n")
    else:
        (work / "README.md").write_text("readme from main\n")
    _git(work, "commit", "-am", "main moves on")
    _git(work, "push", "origin", "main")
    _git(work, "reset", "--hard", "HEAD~1")
    _git(work, "checkout", "ci-fix")
    return work


def _never_called(task: str) -> CodingResult:
    raise AssertionError(f"coding agent must not run for a clean merge: {task}")


def test_clean_merge_commits_without_the_coding_agent(tmp_path: Path) -> None:
    # Arrange
    work = _repo(tmp_path, conflict=False)

    # Act
    merge = merge_base_into_head(str(work), _CTX, baseline={}, resolve_conflicts=_never_called)

    # Assert
    assert merge.resolved_files == ()
    assert merge.commit_sha == head_sha(str(work))
    assert (work / "README.md").read_text() == "readme from main\n"
    assert merge_in_progress(str(work)) is False


def test_conflicts_resolved_by_agent_are_committed_and_reported(tmp_path: Path) -> None:
    # Arrange
    work = _repo(tmp_path, conflict=True)
    tasks: list[str] = []

    def resolve(task: str) -> CodingResult:
        tasks.append(task)
        (work / "package.json").write_text('{"dep": "2.1"}\n')
        (work / "pnpm-lock.yaml").write_text("dep: 2.1\n")
        return CodingResult(success=True, summary="Took 2.x and regenerated the lockfile.")

    # Act
    merge = merge_base_into_head(str(work), _CTX, baseline={}, resolve_conflicts=resolve)

    # Assert
    assert sorted(merge.resolved_files) == ["package.json", "pnpm-lock.yaml"]
    assert merge_in_progress(str(work)) is False
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert _git(work, "status", "--porcelain") == ""
    assert (work / "package.json").read_text() == '{"dep": "2.1"}\n'
    task = tasks[0]
    assert "- package.json: changed on both ci-fix and main" in task
    assert "Do not hand-edit lockfiles (pnpm-lock.yaml)" in task
    assert "pnpm install --lockfile-only" in task


def test_unresolved_conflicts_abort_the_merge_and_name_the_blocked_files(tmp_path: Path) -> None:
    # Arrange
    work = _repo(tmp_path, conflict=True)
    before = head_sha(str(work))

    def resolve(_task: str) -> CodingResult:
        (work / "pnpm-lock.yaml").write_text("dep: 2.0\n")
        return CodingResult(success=True, summary="Regenerated the lockfile; package.json unclear.")

    # Act
    with pytest.raises(GitHubCiFixError) as excinfo:
        merge_base_into_head(str(work), _CTX, baseline={}, resolve_conflicts=resolve)

    # Assert
    error = excinfo.value
    assert error.kind == ERR_MERGE_CONFLICT
    assert "blocked on 1 file(s) a person must decide" in error.message
    assert "package.json (changed on both ci-fix and main)" in error.message
    assert "pnpm-lock.yaml" not in error.message.split("decide:")[1].split(".")[0]
    assert "package.json unclear" in error.message
    assert "no push was made" in error.message
    assert merge_in_progress(str(work)) is False
    assert head_sha(str(work)) == before
    assert _git(work, "status", "--porcelain") == ""


def test_failed_agent_run_aborts_the_merge(tmp_path: Path) -> None:
    # Arrange
    work = _repo(tmp_path, conflict=True)
    before = head_sha(str(work))

    def resolve(_task: str) -> CodingResult:
        return CodingResult(success=False, summary="", error="agent timed out", timed_out=True)

    # Act
    with pytest.raises(GitHubCiFixError) as excinfo:
        merge_base_into_head(str(work), _CTX, baseline={}, resolve_conflicts=resolve)

    # Assert
    assert excinfo.value.kind == ERR_MERGE_CONFLICT
    assert "Coding agent: agent timed out" in excinfo.value.message
    assert merge_in_progress(str(work)) is False
    assert head_sha(str(work)) == before
