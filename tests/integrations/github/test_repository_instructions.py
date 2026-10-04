"""GitHub's answers for the action prompt's REPOSITORY INSTRUCTIONS block."""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from infrastructure.harness_providers import RemoteInstructions, RemoteInstructionsStatus
from integrations.github import repository_instructions as github_instructions
from integrations.github.client import GitHubApiError, GitHubFailureKind

_SOURCE = github_instructions.GITHUB_REPOSITORY_INSTRUCTIONS_SOURCE
_RESOLVED = {"github": {"auth_token": "ghp_" + "a" * 36}}
_FILE_PATH = "repos/acme/payments/contents/AGENTS.md"
_REPO_PATH = "repos/acme/payments"
_NOT_FOUND = GitHubApiError("Not Found", status_code=HTTPStatus.NOT_FOUND)
_RATE_LIMITED = GitHubApiError("limit", kind=GitHubFailureKind.RATE_LIMITED)


def _git_checkout(tmp_path: Path, remotes: Mapping[str, str]) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for name, url in remotes.items():
        subprocess.run(["git", "remote", "add", name, url], cwd=root, check=True)
    return root


class _RecordingClient:
    """A ``GitHubRestClient`` stand-in: answers each path with a payload or raises an error."""

    answers: Mapping[str, Any] = {}
    created: list[dict[str, Any]] = []
    requested: list[str] = []

    def __init__(self, github_token: str | None = None, **kwargs: Any) -> None:
        self.created.append({"token": github_token, **kwargs})

    def request(self, method: str, path: str, **_kwargs: Any) -> Any:
        _ = method
        self.requested.append(path)
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _client_answering(answers: Mapping[str, Any]) -> type[_RecordingClient]:
    """A fresh client class per test, so recorded calls never leak between tests."""
    return type(
        "_Client", (_RecordingClient,), {"answers": answers, "created": [], "requested": []}
    )


def _file_payload(text: str) -> dict[str, Any]:
    encoded = base64.encodebytes(text.encode("utf-8")).decode("ascii")
    return {"type": "file", "encoding": "base64", "content": encoded, "path": "AGENTS.md"}


@pytest.mark.parametrize(
    ("remotes", "expected"),
    [
        ({"origin": "git@github.com:Acme/Payments.git"}, True),
        ({"origin": "https://github.com/acme/payments-fork.git"}, False),
        ({"origin": "https://evil.test/github.com/acme/payments"}, False),
        ({"upstream": "https://github.com/acme/payments"}, False),
    ],
    ids=["scp-any-case", "other-repository", "other-host", "upstream-only"],
)
def test_a_checkout_counts_only_when_its_origin_is_the_repository(
    tmp_path: Path, remotes: Mapping[str, str], expected: bool
) -> None:
    # Arrange
    root = _git_checkout(tmp_path, remotes)

    # Act / Assert
    assert _SOURCE.checkout_matches("acme/payments", root) is expected


def test_the_credential_scope_is_a_digest_that_differs_per_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    for name in ("GITHUB_MCP_AUTH_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    other = {"github": {"auth_token": "ghp_" + "b" * 36}}

    # Act
    scope = _SOURCE.credential_scope(_RESOLVED)

    # Assert: no connection means no read; a cached read never crosses grants.
    assert _SOURCE.credential_scope({}) is None
    assert scope is not None
    assert "a" * 8 not in scope
    assert scope != _SOURCE.credential_scope(other)


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (
            {_FILE_PATH: _file_payload("Run make test.\n")},
            RemoteInstructions(
                RemoteInstructionsStatus.FOUND,
                content=b"Run make test.\n",
                origin="GitHub default branch",
            ),
        ),
        (
            {_FILE_PATH: _NOT_FOUND, _REPO_PATH: {"full_name": "acme/payments"}},
            RemoteInstructions(RemoteInstructionsStatus.MISSING, origin="GitHub default branch"),
        ),
        (
            {_FILE_PATH: _NOT_FOUND, _REPO_PATH: _NOT_FOUND},
            RemoteInstructions(
                RemoteInstructionsStatus.UNAVAILABLE,
                reason="the GitHub connection cannot see acme/payments",
            ),
        ),
        (
            {_FILE_PATH: _RATE_LIMITED},
            RemoteInstructions(
                RemoteInstructionsStatus.UNAVAILABLE, reason="GitHub rate limit reached"
            ),
        ),
        (
            {_FILE_PATH: [{"type": "file", "name": "README.md"}]},
            RemoteInstructions(RemoteInstructionsStatus.MISSING, origin="GitHub default branch"),
        ),
    ],
    ids=["found", "missing", "hidden-repository", "rate-limited", "directory"],
)
def test_the_remote_read_tells_a_missing_file_from_one_it_cannot_see(
    monkeypatch: pytest.MonkeyPatch, answers: Mapping[str, Any], expected: RemoteInstructions
) -> None:
    # Arrange
    client = _client_answering(answers)
    monkeypatch.setattr(github_instructions, "GitHubRestClient", client)

    # Act
    result = _SOURCE.fetch("acme/payments", _RESOLVED)

    # Assert: the default branch (no ref), and a rate limit is reported, never waited out.
    assert result == expected
    assert client.requested[0] == _FILE_PATH
    assert client.created[0]["rate_limit_patience_seconds"] == 0.0
