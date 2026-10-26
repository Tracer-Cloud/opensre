"""Fixture tests for integrations/github/tools/stargazers.py.

Uses monkeypatch to mock GitHubRestClient.request — no live GitHub API calls.
Covers: happy path, missing starred_at timestamps, API error on repo fetch,
API error on stargazers fetch, unexpected payload types.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from unittest.mock import patch

from integrations.github.tools.stargazers import (
    _normalize_days,
    _parse_github_timestamp,
    get_github_star_history,
)

# ── unit tests for pure helpers ───────────────────────────────────────────────


def test_normalize_days_default() -> None:
    assert _normalize_days(None) == 30


def test_normalize_days_clamps_low() -> None:
    assert _normalize_days(0) == 1


def test_normalize_days_clamps_high() -> None:
    assert _normalize_days(999) == 365


def test_normalize_days_passthrough() -> None:
    assert _normalize_days(7) == 7


def test_parse_github_timestamp_valid() -> None:
    result = _parse_github_timestamp("2024-06-01T12:00:00Z")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_github_timestamp_empty_string() -> None:
    assert _parse_github_timestamp("") is None


def test_parse_github_timestamp_none() -> None:
    assert _parse_github_timestamp(None) is None


def test_parse_github_timestamp_invalid() -> None:
    assert _parse_github_timestamp("not-a-date") is None


# ── fixture helpers ───────────────────────────────────────────────────────────


def _make_star_item(days_ago: int) -> dict[str, str]:
    # Build a starred_at timestamp N days before now
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    return {"starred_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _repo_payload(stars: int = 5) -> dict[str, int | str]:
    return {"stargazers_count": stars, "full_name": "opensre/opensre"}


# ── integration-level fixture tests ──────────────────────────────────────────


def test_happy_path_returns_daily_rows() -> None:
    # Repo has 2 stars both within the last 7 days
    star_items = [_make_star_item(1), _make_star_item(3)]

    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        if "stargazers" in path:
            return star_items
        return _repo_payload(stars=2)

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="opensre",
            repo="opensre",
            days=7,
        )

    assert result["available"] is True
    assert result["stargazers_count"] == 2
    assert result["stars_in_window"] == 2
    assert len(result["daily"]) == 7
    assert result["owner"] == "opensre"
    assert result["repo"] == "opensre"


def test_missing_starred_at_returns_unavailable() -> None:
    # Stargazer items exist but have no starred_at field
    star_items = [{"user": "someone"}]

    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        if "stargazers" in path:
            return star_items
        return _repo_payload(stars=1)

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="opensre",
            repo="opensre",
            days=7,
        )

    assert result["available"] is False
    assert "timestamps" in result.get("error", "").lower()


def test_repo_fetch_error_returns_unavailable() -> None:
    from integrations.github.client import GitHubApiError

    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        raise GitHubApiError("Not found", status_code=HTTPStatus.NOT_FOUND)

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="bad",
            repo="repo",
            days=7,
        )

    assert result["available"] is False
    assert result["daily"] == []


def test_stargazers_fetch_error_returns_unavailable() -> None:
    from integrations.github.client import GitHubApiError

    call_count = 0

    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _repo_payload(stars=3)
        raise GitHubApiError("Forbidden", status_code=HTTPStatus.FORBIDDEN)

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="opensre",
            repo="opensre",
            days=7,
        )

    assert result["available"] is False
    assert result["stargazers_count"] == 3


def test_zero_stars_returns_empty_daily_rows() -> None:
    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        if "stargazers" in path:
            return []
        return _repo_payload(stars=0)

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="opensre",
            repo="opensre",
            days=7,
        )

    assert result["available"] is True
    assert result["stars_in_window"] == 0
    assert all(row["stars"] == 0 for row in result["daily"])


def test_unexpected_repo_payload_returns_unavailable() -> None:
    def fake_request(
        method: str, path: str, params: dict | None = None, accept: str | None = None
    ) -> object:
        return "not a dict"

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="opensre",
            repo="opensre",
            days=7,
        )

    assert result["available"] is False
