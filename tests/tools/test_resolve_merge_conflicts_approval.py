"""Tests for the approval, commit and push steps of the merge-conflict tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from integrations.coding_agent import CodingResult
from integrations.git import head_sha, merge_in_progress, merge_ref
from integrations.github import ChecksOutcome
from tools.cross_vendor.resolve_merge_conflicts.runner import resolve_merge

_VERIFY = "tools.cross_vendor.resolve_merge_conflicts.runner.verify_coding_agent"
_RUN = "tools.cross_vendor.resolve_merge_conflicts.runner.run_coding_task"
_WATCH = "tools.cross_vendor.resolve_merge_conflicts.runner.watch_pull_request_checks"


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
    assert asked == [
        "commit the merge of main into feature, push it to origin/feature "
        "and wait for the pull request checks"
    ]
    assert out["checks_state"] == "not_watched"
    assert "not watched because the origin is not a GitHub repository" in out["outcome"]
    assert out["success"] is True and out["pushed"] is True
    assert out["pushed_to"] == "origin/feature"
    assert out["commit_sha"] == _git(bare, "rev-parse", "refs/heads/feature")
    assert out["resolutions"] == ["app.py: 1 conflict (conflict 1 combined)"]
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

    # Assert: the merge commit exists, but the requested operation did not complete.
    assert out["success"] is False and out["pushed"] is False
    assert out["error_kind"] == "push_failed"
    assert out["commit_sha"] == head_sha(str(work))
    assert "did not push it" in out["outcome"]
    assert "push feature to update the pull request" in out["next_step"]


def test_green_checks_end_the_run_and_failed_checks_are_reported(tmp_path: Path) -> None:
    # Arrange
    work, _bare = _stopped_merge_with_origin(tmp_path)
    url = "https://github.com/Tracer-Cloud/opensre/pull/6183"
    green = ChecksOutcome("passed", "all 3 pull request checks passed", pr_url=url)
    red = ChecksOutcome("failed", "checks failed: CI Gate", pr_url=url, failing_checks=("CI Gate",))

    # Act
    with (
        patch(_VERIFY, return_value=(True, "ready")),
        patch(_RUN, side_effect=lambda *_a, **_k: _resolve_app(work)),
        patch(_WATCH, return_value=green) as watch,
    ):
        passed = resolve_merge(str(work), ref=None, model=None, instructions=None, approve=None)
    _git(work, "checkout", "-q", "main")
    (work / "later.txt").write_text("main moved on\n")
    _git(work, "add", "later.txt")
    _git(work, "commit", "-qm", "main moves on")
    _git(work, "checkout", "-q", "feature")
    with patch(_WATCH, return_value=red):
        failed_output = resolve_merge(
            str(work), ref="main", model=None, instructions=None, approve=None
        )

    # Assert
    assert watch.call_args.kwargs["pushed_to"] == "origin/feature"
    assert passed["checks_state"] == "passed" and passed["error_kind"] is None
    assert passed["pull_request_url"] == url
    assert passed["outcome"].endswith(f"all 3 pull request checks passed ({url}).")
    assert passed["next_step"] == "The pull request is green; it is ready for review or merge."
    assert failed_output["error_kind"] == "checks_failed"
    assert failed_output["success"] is False and failed_output["commit_sha"]
    assert failed_output["failing_checks"] == ["CI Gate"]
    assert "but checks failed: CI Gate" in failed_output["outcome"]


def _clean_merge_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """``feature`` tracks ``origin/feature``; ``main`` changed a different file so the merge is clean."""
    bare = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "app.py").write_text("greeting = 'hello'\n")
    (work / "notes.txt").write_text("notes\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "checkout", "-b", "feature")
    (work / "app.py").write_text("greeting = 'hello, world'\n")
    _git(work, "commit", "-am", "feature greeting")
    _git(work, "push", "-u", "origin", "feature")
    _git(work, "checkout", "main")
    (work / "notes.txt").write_text("main notes\n")
    _git(work, "commit", "-am", "main notes")
    _git(work, "checkout", "feature")
    return work, bare


def test_escape_before_a_clean_merge_does_not_push(tmp_path: Path) -> None:
    # Arrange: ESC is already pressed before the conflict-free merge starts.
    work, bare = _clean_merge_with_origin(tmp_path)
    before_local = head_sha(str(work))
    before_remote = _git(bare, "rev-parse", "refs/heads/feature")

    # Act
    out = resolve_merge(
        str(work), ref="main", model=None, instructions=None, cancelled=lambda: True
    )

    # Assert
    assert out["success"] is False
    assert out["error_kind"] == "cancelled"
    assert out["pushed"] is False
    assert head_sha(str(work)) == before_local
    assert _git(bare, "rev-parse", "refs/heads/feature") == before_remote
    assert "Nothing was committed or pushed" in out["error"]


def test_escape_after_a_clean_merge_is_staged_leaves_it_uncommitted(tmp_path: Path) -> None:
    # Arrange: ESC arrives after git staged the clean merge, before the commit and push.
    work, bare = _clean_merge_with_origin(tmp_path)
    before = head_sha(str(work))
    before_remote = _git(bare, "rev-parse", "refs/heads/feature")
    cancelled = False

    def merge_then_cancel(workspace: str, ref: str, *, message: str, commit: bool = True) -> bool:
        nonlocal cancelled
        clean = merge_ref(workspace, ref, message=message, commit=commit)
        cancelled = True
        return clean

    # Act
    with patch(
        "tools.cross_vendor.resolve_merge_conflicts.runner.merge_ref",
        side_effect=merge_then_cancel,
    ):
        out = resolve_merge(
            str(work),
            ref="main",
            model=None,
            instructions=None,
            cancelled=lambda: cancelled,
        )

    # Assert
    assert out["error_kind"] == "cancelled"
    assert out["commit_sha"] is None and out["pushed"] is False
    assert head_sha(str(work)) == before
    assert merge_in_progress(str(work))
    assert _git(bare, "rev-parse", "refs/heads/feature") == before_remote
    assert "Stopped before the commit" in out["error"]


def test_escape_before_the_commit_leaves_the_merge_open(tmp_path: Path) -> None:
    # Arrange: the user presses ESC while the coding agent is still working.
    work, _bare = _stopped_merge_with_origin(tmp_path)
    before = head_sha(str(work))

    # Act
    with (
        patch(_VERIFY, return_value=(True, "ready")),
        patch(_RUN, side_effect=lambda *_a, **_k: _resolve_app(work)),
    ):
        out = resolve_merge(
            str(work), ref=None, model=None, instructions=None, cancelled=lambda: True
        )

    # Assert
    assert out["success"] is False
    assert out["error_kind"] == "cancelled"
    assert out["merge_in_progress"] is True
    assert head_sha(str(work)) == before
    assert "nothing was committed or pushed" in out["error"]


def test_a_clean_merge_is_not_committed_until_approved(tmp_path: Path) -> None:
    # Arrange: feature and main touch different files, so the merge has no conflicts.
    work, bare = _stopped_merge_with_origin(tmp_path)
    _git(work, "merge", "--abort")
    _git(work, "checkout", "-q", "main")
    (work / "other.txt").write_text("from main\n")
    _git(work, "add", "other.txt")
    _git(work, "commit", "-qm", "main adds other")
    _git(work, "checkout", "-q", "feature")
    _git(work, "reset", "-q", "--hard", "HEAD")
    (work / "app.py").write_text("greeting = 'hi'\n")
    _git(work, "commit", "-qam", "match main")
    before = head_sha(str(work))

    # Act
    declined = resolve_merge(
        str(work), ref="main", model=None, instructions=None, approve=lambda _a: False
    )
    approved = resolve_merge(
        str(work), ref=None, model=None, instructions=None, approve=lambda _a: True
    )

    # Assert: declining leaves HEAD untouched with the merge staged; approving commits and pushes.
    assert declined["error_kind"] == "confirmation_denied"
    assert head_sha(str(work)) != before or approved["commit_sha"] != before
    assert declined["merge_in_progress"] is True
    assert approved["success"] is True and approved["pushed"] is True
    assert _git(bare, "rev-parse", "refs/heads/feature") == approved["commit_sha"]
    assert (work / "other.txt").read_text() == "from main\n"


def test_a_merge_the_agent_committed_itself_is_still_approved_and_pushed(tmp_path: Path) -> None:
    # Arrange: the coding agent resolves and commits the merge on its own.
    work, bare = _stopped_merge_with_origin(tmp_path)
    asked: list[str] = []

    def agent_commits(_task: str, **_kwargs: object) -> CodingResult:
        (work / "app.py").write_text("greeting = 'hi, world'\n")
        _git(work, "add", "app.py")
        _git(work, "commit", "-qm", "merge main")
        return CodingResult(success=True, summary="Merged and committed.")

    def approve(action: str) -> bool:
        asked.append(action)
        return True

    # Act
    with (
        patch(_VERIFY, return_value=(True, "ready")),
        patch(_RUN, side_effect=agent_commits),
    ):
        out = resolve_merge(str(work), ref=None, model=None, instructions=None, approve=approve)

    # Assert
    assert asked and asked[0].startswith(
        "push the merge of main into feature, committed by the coding agent"
    )
    assert out["success"] is True and out["pushed"] is True
    assert _git(bare, "rev-parse", "refs/heads/feature") == out["commit_sha"]


def test_escape_during_approval_of_an_agent_committed_merge_stops_the_push(tmp_path: Path) -> None:
    # Arrange: the agent commits the merge; ESC arrives while the approval card is open.
    work, bare = _stopped_merge_with_origin(tmp_path)
    before_remote = _git(bare, "rev-parse", "refs/heads/feature")
    cancelled = False

    def agent_commits(_task: str, **_kwargs: object) -> CodingResult:
        (work / "app.py").write_text("greeting = 'hi, world'\n")
        _git(work, "add", "app.py")
        _git(work, "commit", "-qm", "merge main")
        return CodingResult(success=True, summary="Merged and committed.")

    def approve_then_cancel(_action: str) -> bool:
        nonlocal cancelled
        cancelled = True
        return True

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=agent_commits):
        out = resolve_merge(
            str(work),
            ref=None,
            model=None,
            instructions=None,
            approve=approve_then_cancel,
            cancelled=lambda: cancelled,
        )

    # Assert
    assert out["error_kind"] == "cancelled" and out["success"] is False
    assert out["commit_sha"] == head_sha(str(work)) and out["pushed"] is False
    assert _git(bare, "rev-parse", "refs/heads/feature") == before_remote
