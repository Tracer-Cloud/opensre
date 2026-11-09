"""Open GitHub pull requests from a local workspace.

Resolves the target repository from the workspace's ``origin`` remote and calls
GitHub's REST API to create a pull request from an already-pushed feature branch.
Tokens are resolved through the existing GitHub credential helper and never
appear in the returned payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.repo_scope import detect_git_remote_repo_scope

ERR_GITHUB_TOKEN = "github_token_missing"
ERR_REPO_SCOPE = "repo_scope_unresolved"
ERR_PR_FAILED = "pr_failed"


@dataclass(frozen=True)
class PullRequest:
    """Identity of an opened pull request."""

    url: str
    number: int


class GitHubPullRequestError(Exception):
    """Expected PR-open failure with a stable ``kind`` for callers to map."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def resolve_repo_scope(workspace: str) -> tuple[str, str]:
    """Return ``(owner, repo)`` for *workspace*'s origin remote, or raise."""
    scope = detect_git_remote_repo_scope(workspace)
    if scope is None:
        raise GitHubPullRequestError(
            ERR_REPO_SCOPE,
            "Could not determine the GitHub owner/repo from the workspace's 'origin' remote.",
        )
    return scope


def open_pull_request(
    workspace: str,
    *,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    github_token: str | None = None,
) -> PullRequest:
    """Open a PR from *head_branch* into *base_branch*."""
    token = resolve_github_token(github_token)
    if not token:
        raise GitHubPullRequestError(
            ERR_GITHUB_TOKEN,
            "A GitHub token is required to open a PR. Connect or reconnect GitHub in the OpenSRE app.",
        )

    owner, repo = resolve_repo_scope(workspace)
    client = GitHubRestClient(token)
    try:
        payload = client.request(
            "POST",
            f"repos/{owner}/{repo}/pulls",
            body={
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": body,
                "maintainer_can_modify": True,
            },
        )
    except GitHubApiError as exc:
        raise GitHubPullRequestError(
            ERR_PR_FAILED, f"GitHub rejected the pull request: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise GitHubPullRequestError(
            ERR_PR_FAILED, "Unexpected response shape when opening the PR."
        )

    url = str(payload.get("html_url") or "")
    number_raw = payload.get("number")
    number = int(number_raw) if isinstance(number_raw, int) else 0
    if not url:
        raise GitHubPullRequestError(ERR_PR_FAILED, "GitHub did not return a pull request URL.")
    return PullRequest(url=url, number=number)


__all__ = [
    "ERR_GITHUB_TOKEN",
    "ERR_PR_FAILED",
    "ERR_REPO_SCOPE",
    "GitHubPullRequestError",
    "PullRequest",
    "open_pull_request",
    "resolve_repo_scope",
]
