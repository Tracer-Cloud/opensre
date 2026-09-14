"""Tests for the approval, commit and push steps of the merge-conflict tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from integrations.coding_agent import CodingResult
from integrations.git import head_sha, merge_in_progress
from tools.cross_vendor.resolve_merge_conflicts.runner import resolve_merge

_VERIFY = "tools.cross_vendor.resolve_merge_conflicts.runner.verify_coding_agent"
_RUN = "tools.cross_vendor.resolve_merge_conflicts.runner.run_coding_task"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _stopped_merge_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """``feature`` tracks ``origin/feature`` in a local bare remote; ``main`` conflicts on app.py."""
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "app.py").write_text("greeting = 'hello'\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "checkout", "-b", "feature")
    (work / "app.py").write_text("greeting = 'hello, world'\n")
    _git(work, "commit", "-am", "feature greeting")
    _git(work, "push", "-u", "origin", "feature")
    _git(work, "checkout", "main")
    (work / "app.py").write_text("greeting = 'hi'\n")
    _git(work, "commit", "-am", "main greeting")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge_in_progress(str(work))
    return work, bare


def _resolve_app(work: Path) -> CodingResult:
    (work / "app.py").write_text("greeting = 'hi, world'\n")
    return CodingResult(success=True, summary="Combined both greetings.")


def test_approved_merge_is_committed_and_pushed_to_the_tracked_branch(tmp_path: Path) -> None:
    # Arrange
    work, bare = _stopped_merge_with_origin(tmp_path)
    asked: list[str] = []

    def approve(action: str) -> bool:
        asked.append(action)
        return True

    # Act
    with (
        patch(_VERIFY, return_value=(True, "ready")),
        patch(_RUN, side_effect=lambda *_a, **_k: _resolve_app(work)),
    ):
        out = resolve_merge(str(work), ref=None, model=None, instructions=None, approve=approve)

    # Assert
    assert asked == ["commit the merge of main into feature and push it to origin/feature"]
    assert out["success"] is True and out["pushed"] is True
    assert out["pushed_to"] == "origin/feature"
    assert out["commit_sha"] == _git(bare, "rev-parse", "refs/heads/feature")
    assert out["resolutions"] == [
        "app.py: combined both sides (+1 -1 against feature, +1 -1 against main)"
    ]
    assert "pushed it to origin/feature" in out["outcome"]


def test_declined_approval_leaves_the_resolved_files_uncommitted_then_a_rerun_finishes(
    tmp_path: Path,
) -> None:
    # Arrange
    work, bare = _stopped_merge_with_origin(tmp_path)
    before = head_sha(str(work))
    agent_runs: list[str] = []

    def run_agent(task: str, **_kwargs: object) -> CodingResult:
        agent_runs.append(task)
        return _resolve_app(work)

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=run_agent):
        declined = resolve_merge(
            str(work), ref=None, model=None, instructions=None, approve=lambda _a: False
        )
        finished = resolve_merge(
            str(work), ref=None, model=None, instructions=None, approve=lambda _a: True
        )

    # Assert: nothing moved on decline, and the rerun commits without a second agent run.
    assert declined["success"] is False
    assert declined["error_kind"] == "confirmation_denied"
    assert declined["merge_in_progress"] is True
    assert "ask again to commit and push" in declined["next_step"]
    assert len(agent_runs) == 1
    assert finished["success"] is True and finished["pushed"] is True
    assert finished["commit_sha"] != before
    assert (
        finished["coding_agent_summary"]
        == "The conflicted files were already edited in the working tree."
    )
    assert _git(bare, "rev-parse", "refs/heads/feature") == finished["commit_sha"]


def test_push_failure_keeps_the_local_commit_and_says_so(tmp_path: Path) -> None:
    # Arrange: the remote refuses pushes.
    work, _bare = _stopped_merge_with_origin(tmp_path)
    _git(work, "remote", "set-url", "--push", "origin", str(tmp_path / "missing.git"))

    # Act
    with (
        patch(_VERIFY, return_value=(True, "ready")),
        patch(_RUN, side_effect=lambda *_a, **_k: _resolve_app(work)),
    ):
        out = resolve_merge(str(work), ref=None, model=None, instructions=None, approve=None)

    # Assert
    assert out["success"] is True and out["pushed"] is False
    assert out["error_kind"] == "push_failed"
    assert out["commit_sha"] == head_sha(str(work))
    assert "did not push it" in out["outcome"]
    assert "push feature to update the pull request" in out["next_step"]
