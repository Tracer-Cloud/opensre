"""Tests for the merge-conflict resolution tool against a real temp repository."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from integrations.coding_agent import CodingResult
from integrations.git import GitCommandError, head_sha, merge_in_progress
from integrations.github import PullRequestCheckout
from tests.tools.conftest import BaseToolContract
from tools.cross_vendor.resolve_merge_conflicts import (
    ResolveMergeConflictsTool,
    resolve_merge_conflicts,
)
from tools.cross_vendor.resolve_merge_conflicts.tool import _say
from tools.registry import get_registered_tool_map

_VERIFY = "tools.cross_vendor.resolve_merge_conflicts.runner.verify_coding_agent"
_RUN = "tools.cross_vendor.resolve_merge_conflicts.runner.run_coding_task"
_CHECKOUT = "tools.cross_vendor.resolve_merge_conflicts.tool.checkout_pull_request"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _diverged_repo(tmp_path: Path) -> Path:
    """Work tree on ``feature`` (tracking a local bare origin) where ``main`` changed the same line of ``app.py``."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "app.py").write_text("greeting = 'hello'\n")
    (work / "notes.txt").write_text("notes\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "app.py").write_text("greeting = 'hello, world'\n")
    _git(work, "commit", "-am", "feature greeting")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "feature")
    _git(work, "checkout", "main")
    (work / "app.py").write_text("greeting = 'hi'\n")
    _git(work, "commit", "-am", "main greeting")
    _git(work, "checkout", "feature")
    return work


def _stopped_merge(tmp_path: Path) -> Path:
    work = _diverged_repo(tmp_path)
    merge = subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge.returncode != 0 and merge_in_progress(str(work))
    return work


class TestResolveMergeConflictsContract(BaseToolContract):
    def get_tool_under_test(self) -> ResolveMergeConflictsTool:
        return resolve_merge_conflicts


def test_metadata_is_a_mutating_action_tool_discovered_by_the_registry() -> None:
    # Arrange
    tool = resolve_merge_conflicts

    # Act
    registered = get_registered_tool_map().get(tool.name)

    # Assert
    assert registered is not None
    assert tool.side_effect_level == "mutating"
    assert tool.surfaces == ("action",)
    assert tool.requires_approval is True
    assert tool.is_available({}) is True


def test_resolved_conflicts_are_committed_without_sweeping_in_unrelated_work(
    tmp_path: Path,
) -> None:
    # Arrange: a stopped merge plus a person's unrelated edit that must stay out of the commit.
    work = _stopped_merge(tmp_path)
    (work / "notes.txt").write_text("work in progress\n")
    tasks: list[str] = []

    def resolve(task: str, **_kwargs: object) -> CodingResult:
        tasks.append(task)
        (work / "app.py").write_text("greeting = 'hi, world'\n")
        return CodingResult(success=True, summary="Combined both greetings.")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=resolve):
        out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert out["success"] is True
    assert out["commit_sha"] == head_sha(str(work))
    assert out["resolved_files"] == ["app.py"]
    assert out["merge_in_progress"] is False
    assert out["branch"] == "feature" and out["merged"] == "main"
    assert out["coding_agent_summary"] == "Combined both greetings."
    assert len(_git(work, "log", "-1", "--pretty=%P").split()) == 2
    assert _git(work, "status", "--porcelain").split() == ["M", "notes.txt"]
    assert "- app.py: changed on both feature and main" in tasks[0]
    assert "do not abort, reset, or commit it" in tasks[0]


def test_markers_left_behind_keep_the_merge_open_and_name_the_file(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)
    before = head_sha(str(work))

    def leave_markers(_task: str, **_kwargs: object) -> CodingResult:
        return CodingResult(success=True, summary="Could not decide which greeting to keep.")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=leave_markers):
        out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert out["success"] is False
    assert out["error_kind"] == "conflicts_remain"
    assert out["unresolved_files"] == ["app.py"]
    assert "app.py (changed on both feature and main)" in out["error"]
    assert out["coding_agent_summary"] == "Could not decide which greeting to keep."
    assert out["merge_in_progress"] is True
    assert head_sha(str(work)) == before
    assert out["questions"] == [
        {
            "file": "app.py",
            "question": "How should app.py be resolved?",
            "options": [
                "Keep feature: greeting = 'hello, world'",
                "Take main: greeting = 'hi'",
                "Combine both sides (say how)",
            ],
            "hunks": [{"ours": ["greeting = 'hello, world'"], "theirs": ["greeting = 'hi'"]}],
        }
    ]
    assert "ask_user_choice" in out["next_step"]


def test_user_instructions_reach_the_coding_agent(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)
    tasks: list[str] = []

    def resolve(task: str, **_kwargs: object) -> CodingResult:
        tasks.append(task)
        (work / "app.py").write_text("greeting = 'hello, world'\n")
        return CodingResult(success=True, summary="Kept the feature greeting.")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=resolve):
        out = resolve_merge_conflicts.run(
            workspace=str(work), instructions="keep the feature branch greeting"
        )

    # Assert
    assert out["success"] is True
    assert "Instructions from the user: keep the feature branch greeting" in tasks[0]


def test_an_agent_cut_short_after_settling_every_conflict_still_gets_the_merge_committed(
    tmp_path: Path,
) -> None:
    # Arrange: the agent resolves app.py, then its test run overruns the timeout.
    work = _stopped_merge(tmp_path)

    def resolve(task: str, **_kwargs: object) -> CodingResult:
        del task
        (work / "app.py").write_text("greeting = 'hello, world'\n")
        return CodingResult(
            success=False, summary="Kept the feature greeting.", error="timed out", timed_out=True
        )

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=resolve):
        out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert: committed, with the cut-short run recorded rather than a false "still conflicted".
    assert out["success"] is True and out["unresolved_files"] == []
    assert merge_in_progress(str(work)) is False
    assert out["commit_sha"] == head_sha(str(work))
    assert "ran out of time after resolving every conflict" in out["coding_agent_summary"]
    assert "Kept the feature greeting." in out["coding_agent_summary"]


def test_a_failed_run_that_cleared_markers_is_not_committed(tmp_path: Path) -> None:
    # Arrange: a provider/process failure can leave the tree without markers
    # without having finished the resolution; that is not the timeout recovery.
    work = _stopped_merge(tmp_path)
    before = head_sha(str(work))

    def fail_after_edit(_task: str, **_kwargs: object) -> CodingResult:
        (work / "app.py").write_text("greeting = 'hello, world'\n")
        return CodingResult(
            success=False, summary="Kept the feature greeting.", error="provider error"
        )

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=fail_after_edit):
        out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert out["success"] is False
    assert out["error_kind"] == "execution_error"
    assert "provider error" in out["error"]
    assert out["commit_sha"] is None
    assert out["merge_in_progress"] is True
    assert merge_in_progress(str(work)) is True
    assert head_sha(str(work)) == before
    assert (work / "app.py").read_text() == "greeting = 'hello, world'\n"


def test_no_coding_agent_reports_the_files_and_keeps_the_merge_open(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge(tmp_path)

    # Act
    with patch(_VERIFY, return_value=(False, "pi: not installed")):
        out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert out["error_kind"] == "cli_unavailable"
    assert "pi: not installed" in out["error"]
    assert out["unresolved_files"] == ["app.py"]
    assert merge_in_progress(str(work)) is True


def test_without_a_merge_the_default_branch_is_merged_and_a_repeat_has_nothing_to_do(
    tmp_path: Path,
) -> None:
    # Arrange: feature and main diverge on app.py only.
    work = _diverged_repo(tmp_path)
    _git(work, "checkout", "main")
    (work / "notes.txt").write_text("main notes\n")
    _git(work, "commit", "-am", "main notes")
    _git(work, "checkout", "feature")
    _git(work, "reset", "--hard", "HEAD")
    (work / "app.py").write_text("greeting = 'hi'\n")
    _git(work, "commit", "-am", "match main")

    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")

    # Act: with no ref the default branch is merged; asking again finds nothing to do.
    merged = resolve_merge_conflicts.run(workspace=str(work))
    again = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert merged["merged"] == "origin/main"
    assert again["up_to_date"] is True and again["success"] is True
    assert "nothing to merge, commit or push" in again["outcome"]
    assert again["next_step"] == "Nothing to do."
    assert merged["success"] is True
    assert merged["resolved_files"] == []
    assert merged["commit_sha"] == head_sha(str(work))
    assert _git(work, "log", "-1", "--pretty=%an") == "OpenSRE Agent"
    assert (work / "notes.txt").read_text() == "main notes\n"


def test_the_default_branch_is_fetched_before_it_is_merged(tmp_path: Path) -> None:
    # Arrange: origin/main moves on in another clone; this clone's copy of it is stale.
    work = _diverged_repo(tmp_path)
    _git(work, "checkout", "main")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "remote", "set-head", "origin", "main")
    _git(work, "checkout", "feature")
    (work / "app.py").write_text("greeting = 'hi'\n")
    _git(work, "commit", "-qam", "match main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(other))
    _git(other, "config", "user.email", "o@example.com")
    _git(other, "config", "user.name", "Other")
    (other / "newer.txt").write_text("pushed after this clone last fetched\n")
    _git(other, "add", "newer.txt")
    _git(other, "commit", "-qm", "newer main")
    _git(other, "push", "-q", "origin", "main")

    # Act
    out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert: the merge brought in the commit the clone had not seen.
    assert out["success"] is True and out["merged"] == "origin/main"
    assert (work / "newer.txt").exists()


def test_a_pull_request_is_merged_in_its_own_clone_not_in_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: the "clone" is a repository whose feature branch is behind origin/main,
    # while the current directory is an unrelated folder that must stay untouched.
    clone = _diverged_repo(tmp_path)
    _git(clone, "checkout", "main")
    _git(clone, "push", "-q", "origin", "main")
    _git(clone, "remote", "set-head", "origin", "main")
    _git(clone, "checkout", "feature")
    (clone / "app.py").write_text("greeting = 'hi'\n")
    _git(clone, "commit", "-qam", "match main")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    checkout = PullRequestCheckout(str(clone), "Acme", "widgets", 12, "feature", reused=False)
    asked: list[tuple[str, str]] = []

    def checkout_pull_request(selector: str, *, cwd: str) -> PullRequestCheckout:
        asked.append((selector, cwd))
        return checkout

    # Act
    with patch(_CHECKOUT, checkout_pull_request):
        out = resolve_merge_conflicts.run(pull_request="Acme/widgets#12")

    # Assert
    assert asked == [("Acme/widgets#12", str(elsewhere))]
    assert out["success"] is True
    assert out["workspace"] == str(clone)
    assert out["pull_request"] == "Acme/widgets#12"
    assert out["commit_sha"] == head_sha(str(clone))
    assert list(elsewhere.iterdir()) == []


def test_a_pull_request_that_cannot_be_checked_out_is_reported_without_a_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    monkeypatch.chdir(tmp_path)

    def checkout_pull_request(selector: str, *, cwd: str) -> PullRequestCheckout:
        del selector, cwd
        raise GitCommandError("pr_not_found", "pull request 99 was not found; no push was made.")

    # Act
    with patch(_CHECKOUT, checkout_pull_request):
        out = resolve_merge_conflicts.run(pull_request="99")

    # Assert
    assert out["success"] is False and out["error_kind"] == "pr_not_found"
    assert "not found" in out["error"]
    assert out["pull_request"] == "99"
    assert out["commit_sha"] is None and out["pushed"] is False


def test_checkout_status_escapes_markup_in_the_label_and_workspace() -> None:
    # Arrange
    class _Console:
        def __init__(self) -> None:
            self.printed: list[str] = []

        def print(self, message: str) -> None:
            self.printed.append(message)

    console = _Console()
    scope = type("Scope", (), {"console": console})()

    # Act
    _say(scope, "/tmp/[red]ws", "Acme/[bold]x#1", reused=False)

    # Assert
    assert console.printed == [r"[dim]  Checked out Acme/\[bold]x#1 in /tmp/\[red]ws[/]"]


def test_a_remote_whose_head_is_a_feature_branch_is_not_merged_by_default(tmp_path: Path) -> None:
    # Arrange: origin/HEAD points at "feature", which is no base branch.
    work = _diverged_repo(tmp_path)
    _git(work, "remote", "set-head", "origin", "feature")
    _git(work, "reset", "-q", "--hard", "HEAD")
    before = head_sha(str(work))

    # Act
    out = resolve_merge_conflicts.run(workspace=str(work))

    # Assert
    assert out["success"] is False and out["error_kind"] == "no_merge_in_progress"
    assert "name the branch" in out["error"]
    assert head_sha(str(work)) == before
