"""Tests for GitHubFileContentsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GitHubFileContentsTool import get_github_file_contents
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitHubFileContentsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_github_file_contents.__opensre_registered_tool__


def test_is_available_requires_owner_repo_path() -> None:
    rt = get_github_file_contents.__opensre_registered_tool__
    assert (
        rt.is_available(
            {
                "github": {
                    "connection_verified": True,
                    "owner": "org",
                    "repo": "repo",
                    "path": "main.py",
                }
            }
        )
        is True
    )
    assert (
        rt.is_available({"github": {"connection_verified": True, "owner": "org", "repo": "repo"}})
        is False
    )
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_github_file_contents.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["path"] == "src/main.py"


def test_run_returns_unavailable_when_no_config() -> None:
    with patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None):
        result = get_github_file_contents(owner="org", repo="repo", path="README.md")
    assert result == {
        "source": "github",
        "available": False,
        "error": "GitHub MCP integration is not configured.",
        "file": {},
    }


def test_run_happy_path() -> None:
    fake_result = {
        "is_error": False,
        "tool": "get_file_contents",
        "arguments": {},
        "text": "file content",
        "structured_content": {"name": "main.py", "content": "def main(): pass"},
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitHubFileContentsTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_github_file_contents(
            owner="org",
            repo="repo",
            path="main.py",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert result["file"]["name"] == "main.py"
