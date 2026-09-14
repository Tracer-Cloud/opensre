"""Find the open pull request for a pushed branch and wait for its checks.

Reuses the CI-fix check waiter, so "green" means the same thing after any push.
Without a GitHub origin, a token, or an open pull request for the branch, the
checks are reported as not watched rather than as a failure.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from integrations.git import commit_parents, origin_url
from integrations.github.identity import workspace_public_repository_source
from integrations.github.tools.ci_fix.context import CiFixContext
from integrations.github.tools.ci_fix.errors import GitHubCiFixError
from integrations.github.tools.ci_fix.gh import run_gh_text
from integrations.github.tools.ci_fix.verification import (
    CheckState,
    CheckVerification,
    wait_for_pr_checks,
)

CHECKS_NOT_WATCHED: Final = "not_watched"
_PR_FIELDS: Final = "number,title,url,baseRefName,headRefOid"

Waiter = Callable[..., CheckVerification]


@dataclass(frozen=True)
class ChecksOutcome:
    """What happened to the pull request checks after the merge commit was pushed."""

    state: str
    detail: str
    pr_url: str = ""
    check_names: tuple[str, ...] = ()
    failing_checks: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.state == CheckState.PASSED.value


def watch_pull_request_checks(
    workspace: str,
    *,
    pushed_to: str,
    commit_sha: str,
    wait: Waiter = wait_for_pr_checks,
) -> ChecksOutcome:
    """Wait for the checks of the open pull request whose head is the pushed branch."""
    owner, repo = _github_repository(workspace)
    if not owner:
        return ChecksOutcome(CHECKS_NOT_WATCHED, "the origin is not a GitHub repository")
    branch = _pushed_branch(pushed_to)
    try:
        pull = _open_pull_request(owner, repo, branch)
        if pull is None:
            return ChecksOutcome(CHECKS_NOT_WATCHED, f"no open pull request has head {branch}")
        verification = wait(
            _context(
                owner, repo, pull, branch, previous_head=_previous_head(workspace, commit_sha)
            ),
            github_token=None,
            expected_head_sha=commit_sha,
        )
    except GitHubCiFixError as exc:
        return ChecksOutcome(CHECKS_NOT_WATCHED, exc.message)
    return ChecksOutcome(
        verification.state.value,
        _detail(verification),
        pr_url=str(pull.get("url") or ""),
        check_names=verification.check_names,
        failing_checks=verification.failing_checks,
    )


def _pushed_branch(pushed_to: str) -> str:
    """Branch name inside a push label, ``remote/branch`` or a fork's ``owner:branch``."""
    if ":" in pushed_to:
        return pushed_to.partition(":")[2]
    return pushed_to.partition("/")[2] or pushed_to


def _github_repository(workspace: str) -> tuple[str, str]:
    identity = workspace_public_repository_source({"workspace_repo": origin_url(workspace)})
    github = identity.get("github", {})
    return str(github.get("owner") or ""), str(github.get("repo") or "")


def _open_pull_request(owner: str, repo: str, branch: str) -> dict[str, Any] | None:
    raw = run_gh_text(
        ["pr", "list", "--head", branch, "--state", "open", "--limit", "1", "--json", _PR_FIELDS],
        repo=f"{owner}/{repo}",
        github_token=None,
    )
    try:
        pulls = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return None
    return pulls[0] if isinstance(pulls, list) and pulls and isinstance(pulls[0], dict) else None


def _previous_head(workspace: str, commit_sha: str) -> str:
    parents = commit_parents(workspace, commit_sha)
    return parents[0] if parents else ""


def _context(
    owner: str, repo: str, pull: dict[str, Any], branch: str, *, previous_head: str
) -> CiFixContext:
    return CiFixContext(
        owner=owner,
        repo=repo,
        number=int(pull.get("number") or 0),
        title=str(pull.get("title") or ""),
        url=str(pull.get("url") or ""),
        base_branch=str(pull.get("baseRefName") or ""),
        head_branch=branch,
        head_sha=previous_head or str(pull.get("headRefOid") or ""),
        skipped_check_names=(),
        failing_checks=(),
        task="",
    )


def _detail(verification: CheckVerification) -> str:
    if verification.state is CheckState.PASSED:
        count = len(verification.check_names)
        return f"all {count} pull request check{'s' if count != 1 else ''} passed"
    if verification.state is CheckState.FAILED:
        return f"checks failed: {', '.join(verification.failing_checks) or 'unknown checks'}"
    if verification.state is CheckState.TIMED_OUT:
        return "checks were still running when the wait ended"
    if verification.state is CheckState.SUPERSEDED:
        observed = verification.observed_head_sha[:12] or "another commit"
        return f"another commit ({observed}) replaced the merge commit before checks finished"
    return "GitHub still reports the branch as conflicted, so no checks started"


__all__ = ["CHECKS_NOT_WATCHED", "ChecksOutcome", "watch_pull_request_checks"]
