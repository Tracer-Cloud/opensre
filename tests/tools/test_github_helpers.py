"""Tests for GitHub helper credential mapping and MCP config resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.tool import ERROR_KIND_NOT_FOUND
from core.tool_framework.utils import tool_unavailable
from integrations.github.accessible_repos import AccessibleRepository
from integrations.github.envelope import explain_not_found, normalize_github_tool_result
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


def test_a_404_reads_as_not_found_and_names_the_repositories_that_can_be_reached() -> None:
    """ "GitHub returns 404 for ci/cd" ended in a question the connection could have answered."""
    reachable = (
        AccessibleRepository("vrk7/opensre", fork=True),
        AccessibleRepository("Tracer-Cloud/opensre", fork=False),
    )
    rest = tool_unavailable("github", 'GitHub API error 404: {"message":"Not Found"}', runs=[])
    mcp = tool_unavailable("github", "failed to list runs: GET /repos/ci/cd: 404 Not Found []")
    refused = tool_unavailable("github", "GitHub API error 403: Resource not accessible")

    with patch("integrations.github.envelope.accessible_repositories", return_value=reachable):
        missing_repository = explain_not_found(rest, token="t", owner="ci", repo="cd")
        missing_file = explain_not_found(mcp, token="t", owner="Tracer-Cloud", repo="OpenSRE")
        permission = explain_not_found(refused, token="t", owner="ci", repo="cd")

    # The connection answered: not an outage, and the right repository is one call away.
    assert missing_repository["error_kind"] == ERROR_KIND_NOT_FOUND
    assert missing_repository["failure_state"] == "no_match"
    assert missing_repository["runs"] == []
    assert "It reaches: Tracer-Cloud/opensre, vrk7/opensre." in missing_repository["error"]
    # Something inside a reachable repository is missing: nothing to suggest.
    assert missing_file["error_kind"] == ERROR_KIND_NOT_FOUND
    assert missing_file["error"] == mcp["error"]
    # A refused credential is still the connection's problem.
    assert permission == refused
