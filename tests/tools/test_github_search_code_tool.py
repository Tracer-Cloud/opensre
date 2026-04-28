"""Tests for GitHubSearchCodeTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.GitHubSearchCodeTool import search_github_code
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitHubSearchCodeToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return search_github_code.__opensre_registered_tool__


def test_is_available_requires_connection_verified_owner_repo() -> None:
    rt = search_github_code.__opensre_registered_tool__
    assert (
        rt.is_available({"github": {"connection_verified": True, "owner": "org", "repo": "repo"}})
        is True
    )
    assert rt.is_available({"github": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = search_github_code.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["owner"] == "my-org"
    assert params["repo"] == "my-repo"


def test_run_returns_unavailable_when_no_config() -> None:
    with patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None):
        result = search_github_code(owner="org", repo="repo", query="error")
    assert result == {
        "source": "github",
        "available": False,
        "error": "GitHub MCP integration is not configured.",
        "matches": [],
    }


def test_run_happy_path() -> None:
    fake_result = {
        "is_error": False,
        "tool": "search_code",
        "arguments": {},
        "text": "found 1",
        "structured_content": [{"path": "app.py", "matches": ["line 42"]}],
        "content": [],
    }
    from unittest.mock import MagicMock

    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitHubSearchCodeTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = search_github_code(
            owner="org",
            repo="repo",
            query="error",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert result["matches"] == fake_result["structured_content"]


def test_run_tool_error() -> None:
    fake_result = {
        "is_error": True,
        "text": "GitHub API rate limited",
        "tool": "search_code",
        "arguments": {},
    }
    from unittest.mock import MagicMock

    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitHubSearchCodeTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = search_github_code(
            owner="org",
            repo="repo",
            query="error",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is False
    assert "rate limited" in result["error"]
