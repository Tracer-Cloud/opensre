"""GitHub revision-boundary checks for trusted runbook sources."""

from __future__ import annotations

from http import HTTPStatus
from urllib.parse import quote

import httpx

from config.constants.github import GITHUB_API_BASE_URL

_GITHUB_API_VERSION = "2022-11-28"
_COMPARISON_TIMEOUT_SECONDS = 15.0
_REACHABLE_STATUSES = frozenset({"behind", "identical"})


class GitHubRevisionComparisonError(RuntimeError):
    """Raised when GitHub cannot verify a runbook revision's ancestry."""


def is_github_revision_reachable(
    *,
    owner: str,
    repo: str,
    trusted_revision: str,
    candidate_revision: str,
    auth_token: str,
) -> bool:
    """Return whether ``candidate_revision`` is in ``trusted_revision``'s history."""
    if not auth_token:
        raise GitHubRevisionComparisonError("GitHub authentication is unavailable.")
    repository = f"{quote(owner, safe='')}/{quote(repo, safe='')}"
    comparison = f"{quote(trusted_revision, safe='')}...{quote(candidate_revision, safe='')}"
    url = f"{GITHUB_API_BASE_URL}/repos/{repository}/compare/{comparison}"
    try:
        response = httpx.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {auth_token}",
                "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            },
            timeout=_COMPARISON_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GitHubRevisionComparisonError("GitHub comparison request failed.") from exc
    if response.status_code != HTTPStatus.OK:
        raise GitHubRevisionComparisonError("GitHub comparison request was rejected.")
    try:
        status = str(response.json().get("status") or "").lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise GitHubRevisionComparisonError(
            "GitHub returned an invalid comparison result."
        ) from exc
    return status in _REACHABLE_STATUSES
