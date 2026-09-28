"""Tests for get_github_repository."""

from __future__ import annotations

from unittest.mock import patch

from integrations.github.client import GitHubApiError
from integrations.github.tools.repository import get_github_repository
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGetGitHubRepositoryToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_github_repository.__opensre_registered_tool__


def test_is_available_requires_connection_verified_owner_repo() -> None:
    rt = get_github_repository.__opensre_registered_tool__
    assert (
        rt.is_available({"github": {"connection_verified": True, "owner": "org", "repo": "repo"}})
        is True
    )
    assert rt.is_available({"github": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_classified_credentials() -> None:
    rt = get_github_repository.__opensre_registered_tool__
    sources = mock_agent_state()
    sources["github"] = {
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


def test_run_happy_path() -> None:
    payload = {
        "full_name": "Tracer-Cloud/opensre",
        "html_url": "https://github.com/Tracer-Cloud/opensre",
        "description": "OpenSRE",
        "default_branch": "main",
        "visibility": "public",
        "stargazers_count": 42,
        "watchers_count": 42,
        "forks_count": 7,
        "open_issues_count": 3,
        "subscribers_count": 10,
        "language": "Python",
        "topics": ["sre"],
        "license": {"spdx_id": "MIT"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-02-01T00:00:00Z",
        "pushed_at": "2024-02-02T00:00:00Z",
        "archived": False,
        "disabled": False,
    }
    with patch(
        "integrations.github.tools.repository.GitHubRestClient.request",
        return_value=payload,
    ):
        result = get_github_repository(owner="Tracer-Cloud", repo="opensre", github_token="tok")
    assert result["available"] is True
    assert result["stargazers_count"] == 42
    assert result["repository"]["forks_count"] == 7
    assert result["repository"]["license"] == "MIT"
    assert result["summary"] == "Tracer-Cloud/opensre · 42★ · main · public · Python"
    tool = get_github_repository.__opensre_registered_tool__
    assert "stargazers_count" in tool.description
    assert "never forks_count" in tool.description


def test_run_api_error() -> None:
    with patch(
        "integrations.github.tools.repository.GitHubRestClient.request",
        side_effect=GitHubApiError("not found", status_code=404, path="/repos/o/r"),
    ):
        result = get_github_repository(owner="o", repo="r", github_token="tok")
    assert result["available"] is False
    assert "404" in result["error"]
