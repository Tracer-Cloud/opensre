"""Tests for the GitHub REST integration client."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from http import HTTPStatus
from typing import Any
from urllib import error, request

import pytest

from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token


class _Response:
    def __init__(
        self, payload: Any, *, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _RawResponse(_Response):
    def __init__(self, payload: str, *, headers: dict[str, str] | None = None) -> None:
        super().__init__({}, headers=headers)
        self._raw_payload = payload

    def read(self) -> bytes:
        return self._raw_payload.encode("utf-8")


def test_resolve_github_token_prefers_explicit_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_AUTH_TOKEN", "mcp-token")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")
    assert resolve_github_token("explicit") == "explicit"
    assert resolve_github_token(None) == "mcp-token"
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN")
    assert resolve_github_token(None) == "env-token"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert resolve_github_token(None) == "gh-token"


def test_missing_token_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    client = GitHubRestClient(github_token=None)

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert exc.value.status_code is None
    assert "GitHub token is required" in str(exc.value)


def test_public_read_can_omit_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_headers: dict[str, str] = {}

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        seen_headers.update(req.headers)
        return _Response({"stargazers_count": 42})

    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token=None, allow_unauthenticated_read=True)

    assert client.request("GET", "/repos/Tracer-Cloud/opensre") == {"stargazers_count": 42}
    assert "Authorization" not in seen_headers


def test_public_read_mode_still_rejects_unauthenticated_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    client = GitHubRestClient(github_token=None, allow_unauthenticated_read=True)

    with pytest.raises(GitHubApiError, match="GitHub token is required"):
        client.request("POST", "/repos/Tracer-Cloud/opensre/issues", body={"title": "x"})


def test_paginate_follows_link_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        url = req.full_url
        calls.append(url)
        if "page=2" in url:
            return _Response([{"number": 2}], headers={})
        return _Response(
            [{"number": 1}],
            headers={"Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'},
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    assert client.paginate("/repos/o/r/issues") == [{"number": 1}, {"number": 2}]
    assert len(calls) == 2


def test_paginate_supports_wrapped_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        calls.append(req.full_url)
        if "page=2" in req.full_url:
            return _Response({"check_runs": [{"id": 2}]}, headers={})
        return _Response(
            {"total_count": 2, "check_runs": [{"id": 1}]},
            headers={
                "Link": '<https://api.github.com/repos/o/r/commits/s/check-runs?page=2>; rel="next"'
            },
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    items = client.paginate("/repos/o/r/commits/s/check-runs", collection_key="check_runs")

    assert items == [{"id": 1}, {"id": 2}]
    assert len(calls) == 2


def test_paginate_stops_at_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: paginate() followed every Link-header page with no cap, so
    an endpoint that returns the whole repository's history (e.g.
    /issues/comments, not scoped to one issue) could run to thousands of
    pages on an active repo -- observed live as a 100+ second hang against
    Tracer-Cloud/opensre before this fix."""
    calls: list[str] = []

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        calls.append(req.full_url)
        return _Response(
            [{"id": len(calls)}],
            headers={
                "Link": '<https://api.github.com/repos/o/r/issues/comments?page=X>; rel="next"'
            },
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    items = client.paginate("/repos/o/r/issues/comments", max_pages=3)

    assert len(calls) == 3
    assert len(items) == 3


def test_http_error_preserves_status_and_rate_limit_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(_req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        headers["X-RateLimit-Reset"] = "123"
        raise error.HTTPError(
            url="https://api.github.com/repos/o/r/issues",
            code=403,
            msg="rate limited",
            hdrs=headers,
            fp=None,
        )

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert exc.value.status_code == 403
    assert exc.value.rate_limit_remaining == "0"
    assert exc.value.rate_limit_reset == "123"


def test_api_error_keeps_its_identity_and_details_across_context_manager_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "123"
    http_error = error.HTTPError(
        url="https://api.github.com/repos/o/r/actions/runs",
        code=HTTPStatus.FORBIDDEN,
        msg="rate limited",
        hdrs=headers,
        fp=None,
    )
    propagated: list[GitHubApiError] = []

    def fail_request(_req: request.Request, timeout: int = 0) -> _Response:
        del timeout
        raise http_error

    @contextmanager
    def request_scope() -> Iterator[None]:
        try:
            yield
        except GitHubApiError as exc:
            propagated.append(exc)
            raise

    monkeypatch.setattr("integrations.github.client.request.urlopen", fail_request)
    client = GitHubRestClient(github_token="tok")
    with pytest.raises(GitHubApiError) as caught, request_scope():
        client.request("GET", "/repos/o/r/actions/runs")

    assert caught.value is propagated[0]
    assert caught.value.__cause__ is http_error
    assert caught.value.__traceback__ is not None
    assert caught.value.status_code == HTTPStatus.FORBIDDEN
    assert caught.value.path == "/repos/o/r/actions/runs"
    assert caught.value.rate_limit_remaining == "0"
    assert caught.value.rate_limit_reset == "123"
    assert str(caught.value) == "GitHub API error 403: rate limited"


def test_request_accept_header_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_accept = ""

    def fake_urlopen(req: request.Request, timeout: int = 0) -> _Response:  # noqa: ARG001
        nonlocal seen_accept
        seen_accept = str(req.headers["Accept"])
        return _Response([{"starred_at": "2026-07-27T00:00:00Z"}])

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    assert client.request(
        "GET",
        "/repos/o/r/stargazers",
        accept="application/vnd.github.star+json",
    ) == [{"starred_at": "2026-07-27T00:00:00Z"}]
    assert seen_accept == "application/vnd.github.star+json"


def test_invalid_json_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_req: request.Request, timeout: int = 0) -> _RawResponse:  # noqa: ARG001
        return _RawResponse("not-json")

    monkeypatch.setattr("integrations.github.client.request.urlopen", fake_urlopen)
    client = GitHubRestClient(github_token="tok")

    with pytest.raises(GitHubApiError) as exc:
        client.request("GET", "/repos/o/r/issues")

    assert "invalid JSON" in str(exc.value)
