"""Read a GitHub repository's AGENTS.md for the action prompt.

Registered as the ``github``
:class:`~infrastructure.harness_providers.RepositoryInstructionsSource` from
``integrations/harness_adapters.py``. A local checkout counts only when its
``origin`` remote names the repository. The remote read uses the token a GitHub
REST tool would be handed for the session, reads the default branch, and never
waits out a rate limit; a 404 is reported as a missing file only once the
repository itself is readable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import subprocess
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any

from config.constants.repository_instructions import (
    REPOSITORY_INSTRUCTIONS_FILENAME,
    REPOSITORY_INSTRUCTIONS_READ_BYTES,
)
from infrastructure.harness_providers import RemoteInstructions, RemoteInstructionsStatus
from integrations.github.client import (
    GitHubApiError,
    GitHubFailureKind,
    GitHubRestClient,
    github_failure_kind,
)
from integrations.github.identity import (
    is_github_repository_part,
    workspace_public_repository_source,
)

_ORIGIN = "GitHub default branch"
#: Same bound as the other prompt-path git probes (``repo_scope``).
_GIT_TIMEOUT_SECONDS = 2.0
_FAILURE_REASONS: dict[GitHubFailureKind, str] = {
    GitHubFailureKind.RATE_LIMITED: "GitHub rate limit reached",
    GitHubFailureKind.UNAUTHORIZED: "GitHub refused the connection's token",
    GitHubFailureKind.TLS_UNTRUSTED: "GitHub's certificate was not trusted",
    GitHubFailureKind.UNREACHABLE: "GitHub was unreachable",
    GitHubFailureKind.SERVER_ERROR: "GitHub returned a server error",
    GitHubFailureKind.INVALID_RESPONSE: "GitHub returned an unreadable response",
}


class GithubRepositoryInstructionsSource:
    """AGENTS.md answers for GitHub repositories, named ``owner/repo``."""

    vendor = "github"
    label = "GitHub"

    def checkout_matches(self, repository: str, root: Path) -> bool:
        """True when ``root``'s ``origin`` remote is ``repository`` on github.com."""
        github = workspace_public_repository_source({"workspace_repo": _origin_url(root)}).get(
            "github", {}
        )
        owner, repo = str(github.get("owner") or ""), str(github.get("repo") or "")
        return bool(owner and repo) and f"{owner}/{repo}".casefold() == repository.casefold()

    def credential_scope(self, resolved_integrations: Mapping[str, Any]) -> str | None:
        """A digest of the session's GitHub token, so a cached read never crosses grants."""
        token = _token(resolved_integrations)
        if not token:
            return None
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def fetch(
        self, repository: str, resolved_integrations: Mapping[str, Any]
    ) -> RemoteInstructions:
        """Read the root AGENTS.md of the default branch through the GitHub REST API."""
        owner, _, repo = repository.partition("/")
        if not (is_github_repository_part(owner) and is_github_repository_part(repo)):
            return _unavailable(f"{repository} is not a GitHub owner/repo name")
        client = GitHubRestClient(_token(resolved_integrations), rate_limit_patience_seconds=0.0)
        try:
            payload = client.request(
                "GET", f"repos/{owner}/{repo}/contents/{REPOSITORY_INSTRUCTIONS_FILENAME}"
            )
        except GitHubApiError as exc:
            if exc.status_code == HTTPStatus.NOT_FOUND:
                return _missing_or_hidden(client, owner, repo)
            return _unavailable(_failure_reason(exc))
        return _file_contents(payload)


def _token(resolved_integrations: Mapping[str, Any]) -> str:
    """The token a GitHub REST tool would be handed; imported on first use (it loads ``core.tool``)."""
    from integrations.github.rest_token import resolved_github_rest_token

    return resolved_github_rest_token(resolved_integrations)


def _origin_url(root: Path) -> str:
    """The checkout's configured ``origin`` URL; "" when unset or git cannot answer."""
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _missing_or_hidden(client: GitHubRestClient, owner: str, repo: str) -> RemoteInstructions:
    """A 404 on the file: missing when the repository is readable, else unavailable.

    GitHub answers 404 for a private repository the token cannot see, so the
    file's 404 alone does not prove there is no AGENTS.md.
    """
    try:
        client.request("GET", f"repos/{owner}/{repo}")
    except GitHubApiError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return _unavailable(f"the GitHub connection cannot see {owner}/{repo}")
        return _unavailable(_failure_reason(exc))
    return RemoteInstructions(RemoteInstructionsStatus.MISSING, origin=_ORIGIN)


def _file_contents(payload: Any) -> RemoteInstructions:
    """The decoded file, or missing when the path is not a regular file (a directory or link)."""
    if not isinstance(payload, dict) or payload.get("type") != "file":
        return RemoteInstructions(RemoteInstructionsStatus.MISSING, origin=_ORIGIN)
    encoded = payload.get("content")
    if payload.get("encoding") != "base64" or not isinstance(encoded, str):
        # Files over 1 MB come back without their text.
        return _unavailable("GitHub did not return the file's text")
    try:
        content = base64.b64decode(encoded)
    except (binascii.Error, ValueError):
        return _unavailable(_FAILURE_REASONS[GitHubFailureKind.INVALID_RESPONSE])
    # One byte past the read limit tells core the file was cut.
    return RemoteInstructions(
        RemoteInstructionsStatus.FOUND,
        content=content[: REPOSITORY_INSTRUCTIONS_READ_BYTES + 1],
        origin=_ORIGIN,
    )


def _failure_reason(exc: GitHubApiError) -> str:
    return _FAILURE_REASONS.get(github_failure_kind(exc), "GitHub refused the read")


def _unavailable(reason: str) -> RemoteInstructions:
    return RemoteInstructions(RemoteInstructionsStatus.UNAVAILABLE, reason=reason)


GITHUB_REPOSITORY_INSTRUCTIONS_SOURCE = GithubRepositoryInstructionsSource()


__all__ = ["GITHUB_REPOSITORY_INSTRUCTIONS_SOURCE", "GithubRepositoryInstructionsSource"]
