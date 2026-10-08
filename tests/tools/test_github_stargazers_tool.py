"""Tests for get_github_star_history."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from integrations.github.client import GitHubApiError
from integrations.github.tools.stargazers import get_github_star_history
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGetGitHubStarHistoryToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_github_star_history.__opensre_registered_tool__


def test_is_available_requires_connection_verified_owner_repo() -> None:
    rt = get_github_star_history.__opensre_registered_tool__
    assert (
        rt.is_available(
            {
                "github": {
                    "connection_origin": "webapp",
                    "auth_token": "app-token",
                    "connection_verified": True,
                    "owner": "org",
                    "repo": "repo",
                }
            }
        )
        is True
    )
    assert (
        rt.is_available(
            {
                "github": {
                    "connection_origin": "webapp",
                    "auth_token": "app-token",
                    "connection_verified": True,
                }
            }
        )
        is False
    )
    assert rt.is_available({}) is False


def test_is_available_for_workspace_public_repository() -> None:
    rt = get_github_star_history.__opensre_registered_tool__

    assert (
        rt.is_available(
            {
                "github": {
                    "connection_origin": "webapp",
                    "auth_token": "app-token",
                    "connection_verified": False,
                    "public_repository": True,
                    "owner": "Tracer-Cloud",
                    "repo": "opensre",
                }
            }
        )
        is True
    )


def test_extract_params_maps_classified_credentials() -> None:
    rt = get_github_star_history.__opensre_registered_tool__
    sources = mock_agent_state()
    sources["github"] = {
        "connection_origin": "webapp",
        "connection_verified": True,
        "owner": "Tracer-Cloud",
        "repo": "opensre",
        "url": "https://api.githubcopilot.com/mcp/",
        "mode": "streamable-http",
        "auth_token": "ghp_test",
    }
    params = rt.extract_params(sources)
    assert params["owner"] == "Tracer-Cloud"
    assert params["repo"] == "opensre"
    assert params["github_token"] == "ghp_test"


def test_workspace_scope_is_hidden_from_model_input_schema() -> None:
    rt = get_github_star_history.__opensre_registered_tool__

    properties = rt.public_input_schema["properties"]

    assert "owner" not in properties
    assert "repo" not in properties
    assert properties.keys() == {"days"}


def test_extract_params_marks_workspace_repository_for_public_reads() -> None:
    rt = get_github_star_history.__opensre_registered_tool__

    params = rt.extract_params(
        {
            "github": {
                "connection_origin": "webapp",
                "auth_token": "app-token",
                "connection_verified": False,
                "public_repository": True,
                "owner": "Tracer-Cloud",
                "repo": "opensre",
            }
        }
    )

    assert params["public_repository"] is True


def test_run_allows_anonymous_reads_only_for_public_repository_source() -> None:
    seen_unauthenticated_read_flags: list[bool] = []

    class _StubClient:
        def __init__(self, _token: str | None, *, allow_unauthenticated_read: bool) -> None:
            seen_unauthenticated_read_flags.append(allow_unauthenticated_read)

        def request(self, _method: str, path: str, **_kwargs: Any) -> dict[str, int] | list[dict]:
            if path == "/repos/o/r":
                return {"stargazers_count": 0}
            return []

    with patch("integrations.github.tools.stargazers.GitHubRestClient", _StubClient):
        get_github_star_history(
            github_connection_origin="webapp", github_token="app-token", owner="o", repo="r"
        )
        get_github_star_history(
            github_connection_origin="webapp",
            github_token="app-token",
            owner="o",
            repo="r",
            public_repository=True,
        )

    assert seen_unauthenticated_read_flags == [False, True]


def test_run_scans_newest_pages_until_window_is_covered() -> None:
    repo_payload = {"stargazers_count": 250}
    newest_page = [
        {"starred_at": "2026-07-27T09:00:00Z"},
        {"starred_at": "2026-07-26T18:00:00Z"},
    ]
    older_page = [{"starred_at": "2026-07-24T23:59:59Z"}]

    def fake_request(method: str, path: str, **kwargs):
        if path == "/repos/Tracer-Cloud/opensre":
            return repo_payload
        if path == "/repos/Tracer-Cloud/opensre/stargazers":
            page = kwargs["params"]["page"]
            if page == 3:
                assert kwargs["accept"] == "application/vnd.github.star+json"
                return newest_page
            if page == 2:
                return older_page
        raise AssertionError((method, path, kwargs))

    with (
        patch("integrations.github.tools.stargazers._now_utc") as now_utc,
        patch(
            "integrations.github.tools.stargazers.GitHubRestClient.request",
            side_effect=fake_request,
        ) as request_mock,
    ):
        now_utc.return_value = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        result = get_github_star_history(
            github_connection_origin="webapp",
            owner="Tracer-Cloud",
            repo="opensre",
            days=3,
            github_token="tok",
        )

    assert result["available"] is True
    assert result["stargazers_count"] == 250
    assert result["stars_in_window"] == 2
    assert result["daily"] == [
        {"date": "2026-07-25", "stars": 0},
        {"date": "2026-07-26", "stars": 1},
        {"date": "2026-07-27", "stars": 1},
    ]
    assert result["complete"] is True
    assert result["pages_scanned"] == 2
    stargazer_calls = [
        call
        for call in request_mock.call_args_list
        if call.args[1] == "/repos/Tracer-Cloud/opensre/stargazers"
    ]
    assert [call.kwargs["params"]["page"] for call in stargazer_calls] == [3, 2]


def test_run_returns_partial_when_page_cap_is_hit() -> None:
    repo_payload = {"stargazers_count": 5_000}

    def fake_request(_method: str, path: str, **kwargs):
        if path == "/repos/o/r":
            return repo_payload
        page = kwargs["params"]["page"]
        return [{"starred_at": f"2026-07-{(page % 27) + 1:02d}T00:00:00Z"}]

    with (
        patch("integrations.github.tools.stargazers._now_utc") as now_utc,
        patch(
            "integrations.github.tools.stargazers.GitHubRestClient.request",
            side_effect=fake_request,
        ),
    ):
        now_utc.return_value = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        result = get_github_star_history(
            github_connection_origin="webapp", owner="o", repo="r", days=365, github_token="tok"
        )

    assert result["available"] is True
    assert result["complete"] is False
    assert result["pages_scanned"] == 30
    assert "Stopped after 30" in result["warning"]


def test_run_reports_anonymous_listing_auth_error_with_current_count() -> None:
    def fake_request(_method: str, path: str, **_kwargs):
        if path == "/repos/o/r":
            return {"stargazers_count": 101}
        raise GitHubApiError(
            "requires authentication",
            status_code=401,
            path="/repos/o/r/stargazers",
        )

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp", owner="o", repo="r", github_token="tok"
        )

    assert result["available"] is False
    assert result["stargazers_count"] == 101
    assert "authentication is required" in result["error"]
    assert "current repository star count is still available" in result["error"]


def test_run_reports_missing_starred_at_media_type() -> None:
    def fake_request(_method: str, path: str, **_kwargs):
        if path == "/repos/o/r":
            return {"stargazers_count": 1}
        return [{"login": "octocat"}]

    with patch(
        "integrations.github.tools.stargazers.GitHubRestClient.request",
        side_effect=fake_request,
    ):
        result = get_github_star_history(
            github_connection_origin="webapp", owner="o", repo="r", github_token="tok"
        )

    assert result["available"] is False
    assert "did not include stargazer timestamps" in result["error"]
