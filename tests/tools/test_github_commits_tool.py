"""Tests for GitHubCommitsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GitHubCommitsTool import list_github_commits
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitHubCommitsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_github_commits.__opensre_registered_tool__


def test_is_available_requires_connection_owner_repo() -> None:
    rt = list_github_commits.__opensre_registered_tool__
    assert (
        rt.is_available({"github": {"connection_verified": True, "owner": "org", "repo": "repo"}})
        is True
    )
    assert rt.is_available({"github": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_github_commits.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["owner"] == "my-org"
    assert params["repo"] == "my-repo"


def test_run_returns_unavailable_when_no_config() -> None:
    with patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None):
        result = list_github_commits(owner="org", repo="repo")
    assert result["available"] is False


def test_run_happy_path() -> None:
    fake_result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": "2 commits",
        "structured_content": [
            {"sha": "abc", "message": "fix bug"},
            {"sha": "def", "message": "add feature"},
        ],
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitHubCommitsTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = list_github_commits(
            owner="org",
            repo="repo",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert len(result["commits"]) == 2
