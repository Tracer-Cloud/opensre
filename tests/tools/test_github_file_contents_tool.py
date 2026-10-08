"""Tests for GitHubFileContentsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.github.tools.file_contents import get_github_file_contents
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
                    "connection_origin": "webapp",
                    "auth_token": "app-token",
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
        is False
    )
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_github_file_contents.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["path"] == "src/main.py"


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
        patch("integrations.github.helpers.github_mcp_config_from_env", return_value=None),
        patch(
            "integrations.github.helpers.build_github_mcp_config",
            return_value=mock_config,
        ),
        patch(
            "integrations.github.tools.file_contents.call_github_mcp_tool", return_value=fake_result
        ),
    ):
        result = get_github_file_contents(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            path="main.py",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert result["file"]["name"] == "main.py"


def test_run_falls_back_to_resource_text_when_structured_content_absent() -> None:
    """Regression: the real GitHub Copilot MCP server never populates
    structuredContent for get_file_contents — confirmed live. Its `text` is a
    status line ("successfully downloaded..."), not JSON, so the fallback has
    to read the file body from the resource_text content item instead."""
    fake_result = {
        "is_error": False,
        "tool": "get_file_contents",
        "arguments": {},
        "text": "successfully downloaded text file (SHA: abc123)",
        "structured_content": None,
        "content": [
            {"type": "text", "text": "successfully downloaded text file (SHA: abc123)"},
            {
                "type": "resource_text",
                "uri": "repo://org/repo/sha/abc123/contents/main.py",
                "text": "def main(): pass",
            },
        ],
    }
    mock_config = MagicMock()
    with (
        patch("integrations.github.helpers.github_mcp_config_from_env", return_value=None),
        patch(
            "integrations.github.helpers.build_github_mcp_config",
            return_value=mock_config,
        ),
        patch(
            "integrations.github.tools.file_contents.call_github_mcp_tool", return_value=fake_result
        ),
    ):
        result = get_github_file_contents(
            github_connection_origin="webapp",
            owner="org",
            repo="repo",
            path="main.py",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["available"] is True
    assert result["file"]["content"] == "def main(): pass"
