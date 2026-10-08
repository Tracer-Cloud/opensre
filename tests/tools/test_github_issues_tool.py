"""Tests for GitHubIssuesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.github.tools.issues import search_github_issues
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitHubIssuesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_github_issues.__opensre_registered_tool__


def test_is_available_requires_connection_verified_owner_repo() -> None:
    rt = search_github_issues.__opensre_registered_tool__
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


def test_extract_params_maps_fields() -> None:
    rt = search_github_issues.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["owner"] == "my-org"
    assert params["repo"] == "my-repo"


def test_run_happy_path() -> None:
    fake_result = {
        "is_error": False,
        "tool": "search_issues",
        "arguments": {},
        "text": "found 1",
        "structured_content": [{"number": 1, "title": "crash on windows"}],
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("integrations.github.helpers.github_mcp_config_from_env", return_value=None),
        patch(
            "integrations.github.helpers.build_github_mcp_config",
            return_value=mock_config,
        ),
        patch("integrations.github.tools.issues.call_github_mcp_tool", return_value=fake_result),
    ):
        result = search_github_issues(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            query="crash",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert result["issues"] == fake_result["structured_content"]


def test_run_tool_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "GitHub API rate limited",
        "tool": "search_issues",
        "arguments": {},
    }
    mock_config = MagicMock()
    with (
        patch("integrations.github.helpers.github_mcp_config_from_env", return_value=None),
        patch(
            "integrations.github.helpers.build_github_mcp_config",
            return_value=mock_config,
        ),
        patch("integrations.github.tools.issues.call_github_mcp_tool", return_value=fake_result),
    ):
        result = search_github_issues(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            query="crash",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is False
    assert "rate limited" in result["error"]
