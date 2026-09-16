"""Tests for waiting on the pull request checks after the merge commit is pushed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from integrations.github import CHECKS_NOT_WATCHED, watch_pull_request_checks
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import ERR_GITHUB_TOKEN, GitHubCiFixError
from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification

_GH = "integrations.github.pull_request_checks.run_gh_text"
_PULL = {
    "number": 6183,
    "title": "feat(slack): add proactive messaging policies",
    "url": "https://github.com/Tracer-Cloud/opensre/pull/6183",
    "baseRefName": "main",
    "headRefOid": "96404ddf",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _github_clone(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose origin is GitHub, with one merge commit on top of ``init``."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "feature", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "a.txt").write_text("a\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-q", "-b", "other")
    (work / "b.txt").write_text("b\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "other")
    _git(work, "checkout", "-q", "feature")
    _git(work, "merge", "--no-ff", "-m", "merge other", "other")
    _git(work, "remote", "add", "origin", "https://github.com/Tracer-Cloud/opensre.git")
    return work, _git(work, "rev-parse", "HEAD")


def test_checks_are_watched_on_the_open_pull_request_of_the_pushed_branch(
    tmp_path: Path,
) -> None:
    # Arrange
    work, sha = _github_clone(tmp_path)
    waited: list[tuple[CiFixContext, str]] = []

    def wait(
        ctx: CiFixContext, *, github_token: str | None, expected_head_sha: str
    ) -> CheckVerification:
        waited.append((ctx, expected_head_sha))
        return CheckVerification(state=CheckState.PASSED, check_names=("CI Gate", "quality"))

    # Act
    with patch(_GH, return_value=json.dumps([_PULL])) as gh:
        outcome = watch_pull_request_checks(
            str(work), pushed_to="origin/feat/proactive_messaging", commit_sha=sha, wait=wait
        )

    # Assert
    assert gh.call_args.kwargs["repo"] == "Tracer-Cloud/opensre"
    assert "--head" in gh.call_args.args[0] and "feat/proactive_messaging" in gh.call_args.args[0]
    ctx, expected = waited[0]
    assert (ctx.owner, ctx.repo, ctx.number, ctx.head_branch) == (
        "Tracer-Cloud",
        "opensre",
        6183,
        "feat/proactive_messaging",
    )
    assert ctx.head_sha == _git(work, "rev-parse", "HEAD^1")
    assert expected == sha
    assert outcome.passed is True
    assert outcome.detail == "all 2 pull request checks passed"
    assert outcome.pr_url == _PULL["url"]


def test_failed_checks_are_named(tmp_path: Path) -> None:
    # Arrange
    work, sha = _github_clone(tmp_path)

    def wait(*_args: object, **_kwargs: object) -> CheckVerification:
        return CheckVerification(
            state=CheckState.FAILED, check_names=("CI Gate",), failing_checks=("CI Gate",)
        )

    # Act
    with patch(_GH, return_value=json.dumps([_PULL])):
        outcome = watch_pull_request_checks(
            str(work), pushed_to="origin/feature", commit_sha=sha, wait=wait
        )

    # Assert
    assert outcome.state == "failed"
    assert outcome.failing_checks == ("CI Gate",)
    assert outcome.detail == "checks failed: CI Gate"


def test_checks_are_not_watched_without_a_pull_request_or_a_token(tmp_path: Path) -> None:
    # Arrange
    work, sha = _github_clone(tmp_path)

    def never(*_args: object, **_kwargs: object) -> CheckVerification:
        raise AssertionError("must not wait without a pull request")

    # Act
    with patch(_GH, return_value="[]"):
        no_pull = watch_pull_request_checks(
            str(work), pushed_to="origin/feature", commit_sha=sha, wait=never
        )
    with patch(_GH, side_effect=GitHubCiFixError(ERR_GITHUB_TOKEN, "A GitHub token is required.")):
        no_token = watch_pull_request_checks(
            str(work), pushed_to="origin/feature", commit_sha=sha, wait=never
        )

    # Assert
    assert no_pull.state == CHECKS_NOT_WATCHED
    assert "no open pull request has head feature" in no_pull.detail
    assert no_token.state == CHECKS_NOT_WATCHED
    assert "GitHub token" in no_token.detail


def test_a_non_github_origin_is_not_watched(tmp_path: Path) -> None:
    # Arrange
    work, sha = _github_clone(tmp_path)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "local.git"))

    # Act
    with patch(_GH) as gh:
        outcome = watch_pull_request_checks(str(work), pushed_to="origin/feature", commit_sha=sha)

    # Assert
    assert outcome.state == CHECKS_NOT_WATCHED
    assert gh.call_count == 0


def test_a_fork_push_label_still_finds_the_pull_request_by_branch(tmp_path: Path) -> None:
    # Arrange
    work, sha = _github_clone(tmp_path)

    def wait(
        ctx: CiFixContext, *, github_token: str | None, expected_head_sha: str
    ) -> CheckVerification:
        return CheckVerification(state=CheckState.PASSED, check_names=("CI Gate",))

    # Act
    with patch(_GH, return_value=json.dumps([_PULL])) as gh:
        outcome = watch_pull_request_checks(
            str(work), pushed_to="contributor:feat/proactive_messaging", commit_sha=sha, wait=wait
        )

    # Assert
    argv = gh.call_args.args[0]
    assert argv[argv.index("--head") + 1] == "feat/proactive_messaging"
    assert outcome.passed
