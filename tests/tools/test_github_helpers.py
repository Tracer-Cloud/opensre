"""Tests for GitHub helper credential mapping and MCP config resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.github.envelope import normalize_github_tool_result
from integrations.github.helpers import (
    github_creds,
    resolve_github_mcp_config,
)
from integrations.github.mcp import DEFAULT_GITHUB_MCP_MODE


def test_github_creds_maps_classified_integration_fields() -> None:
    creds = github_creds(
        {
            "connection_origin": "webapp",
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "command": "",
            "args": [],
        }
    )
    assert creds == {
        "github_connection_origin": "webapp",
        "github_url": "https://api.githubcopilot.com/mcp/",
        "github_mode": "streamable-http",
        "github_token": "ghp_test",
    }


def test_github_creds_prefers_legacy_tool_field_names() -> None:
    creds = github_creds(
        {
            "github_url": "http://github.example.com/mcp",
            "connection_origin": "webapp",
            "github_mode": "sse",
            "github_token": "legacy-token",
            "github_command": "gh-mcp",
            "github_args": ["--stdio"],
        }
    )
    assert creds == {
        "github_connection_origin": "webapp",
        "github_url": "http://github.example.com/mcp",
        "github_mode": "sse",
        "github_token": "legacy-token",
        "github_command": "gh-mcp",
        "github_args": ["--stdio"],
    }


def test_github_creds_omits_empty_defaults() -> None:
    assert github_creds({}) == {}


def test_resolve_github_mcp_config_uses_env_when_no_overrides() -> None:
    env_config = MagicMock()
    with patch(
        "integrations.github.helpers.github_mcp_config_from_env",
        return_value=env_config,
    ):
        assert resolve_github_mcp_config(None, None, None) is env_config


def test_resolve_github_mcp_config_builds_when_token_present() -> None:
    env_config = MagicMock()
    built = MagicMock()
    with (
        patch(
            "integrations.github.helpers.github_mcp_config_from_env",
            return_value=env_config,
        ),
        patch("integrations.github.helpers.build_github_mcp_config", return_value=built) as builder,
    ):
        result = resolve_github_mcp_config(None, None, "ghp_test")
    assert result is built
    builder.assert_called_once()
    payload = builder.call_args.args[0]
    assert payload["auth_token"] == "ghp_test"
    assert payload["mode"] == env_config.mode


def test_resolve_github_mcp_config_does_not_treat_default_mode_as_override() -> None:
    env_config = MagicMock()
    with patch(
        "integrations.github.helpers.github_mcp_config_from_env",
        return_value=env_config,
    ):
        assert resolve_github_mcp_config(None, DEFAULT_GITHUB_MCP_MODE, None) is env_config


def test_normalize_github_tool_result_parses_text_when_structured_content_absent() -> None:
    """Regression: the real GitHub Copilot MCP server never populates
    structuredContent for list_commits/search_issues/search_code/
    get_repository_tree — confirmed live against Tracer-Cloud/opensre. Every
    caller that did ``payload.pop("structured_content", None)`` got None on
    every real call, even though ``text`` carried the same data as a raw
    JSON string."""
    result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": '[{"sha": "abc", "message": "fix bug"}]',
        "structured_content": None,
        "content": [],
    }
    payload = normalize_github_tool_result(result)
    assert payload["structured_content"] == [{"sha": "abc", "message": "fix bug"}]


def test_normalize_github_tool_result_prefers_native_structured_content() -> None:
    result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": '[{"sha": "should-not-be-used"}]',
        "structured_content": [{"sha": "native"}],
        "content": [],
    }
    payload = normalize_github_tool_result(result)
    assert payload["structured_content"] == [{"sha": "native"}]


def test_normalize_github_tool_result_stays_none_on_non_json_text() -> None:
    # get_file_contents' text is a status line ("successfully downloaded..."),
    # not JSON — the fallback must not raise, just leave structured_content None.
    result = {
        "is_error": False,
        "tool": "get_file_contents",
        "arguments": {},
        "text": "successfully downloaded text file (SHA: abc123)",
        "structured_content": None,
        "content": [],
    }
    payload = normalize_github_tool_result(result)
    assert payload["structured_content"] is None
