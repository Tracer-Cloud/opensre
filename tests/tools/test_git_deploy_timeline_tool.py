"""Tests for GitDeployTimelineTool (function-based, @tool decorated)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.tools.GitDeployTimelineTool import (
    DEFAULT_WINDOW_MINUTES,
    MAX_PER_PAGE,
    MAX_WINDOW_MINUTES,
    _resolve_window,
    _summarize_commit,
    get_git_deploy_timeline,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitDeployTimelineToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_git_deploy_timeline.__opensre_registered_tool__


def test_is_available_requires_connection_owner_repo() -> None:
    rt = get_git_deploy_timeline.__opensre_registered_tool__
    assert (
        rt.is_available({"github": {"connection_verified": True, "owner": "org", "repo": "repo"}})
        is True
    )
    assert rt.is_available({"github": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_git_deploy_timeline.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["owner"] == "my-org"
    assert params["repo"] == "my-repo"
    assert params["branch"] == "main"


def test_run_returns_unavailable_when_no_config() -> None:
    with patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None):
        result = get_git_deploy_timeline(owner="org", repo="repo")
    assert result["available"] is False
    assert result["commits"] == []


def test_run_happy_path_summarizes_commits() -> None:
    fake_result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": "2 commits",
        "structured_content": [
            {
                "sha": "abcdef0123456789",
                "html_url": "https://github.com/org/repo/commit/abcdef0",
                "commit": {
                    "author": {"name": "Alice", "date": "2026-04-20T09:15:00Z"},
                    "committer": {"date": "2026-04-20T09:15:30Z"},
                    "message": "fix: null deref on empty payload\n\nAdditional body text",
                },
            },
            {
                "sha": "fedcba9876543210",
                "html_url": "https://github.com/org/repo/commit/fedcba9",
                "commit": {
                    "author": {"name": "Bob", "date": "2026-04-20T10:00:00Z"},
                    "committer": {"date": "2026-04-20T10:00:10Z"},
                    "message": "feat: add retry on 502",
                },
            },
        ],
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    assert result["available"] is True
    assert result["commits_count"] == 2
    assert len(result["commits"]) == 2

    first = result["commits"][0]
    assert first["sha"] == "abcdef0123456789"
    assert first["short_sha"] == "abcdef0"
    assert first["author_name"] == "Alice"
    # Only the subject line of the message is kept; body is dropped.
    assert first["message_subject"] == "fix: null deref on empty payload"
    assert first["url"] == "https://github.com/org/repo/commit/abcdef0"

    window = result["window"]
    assert "since" in window and "until" in window
    assert window["branch"] == "main"


def test_run_passes_time_window_and_branch_to_mcp() -> None:
    mock_config = MagicMock()
    captured: dict[str, object] = {}

    def _fake_call(config, name, arguments):  # noqa: ARG001
        captured["name"] = name
        captured["arguments"] = arguments
        return {"is_error": False, "text": "", "structured_content": [], "content": []}

    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", side_effect=_fake_call),
    ):
        get_git_deploy_timeline(
            owner="org",
            repo="repo",
            branch="release",
            since="2026-04-20T08:00:00Z",
            until="2026-04-20T10:00:00Z",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    assert captured["name"] == "list_commits"
    args = captured["arguments"]
    assert args["owner"] == "org"
    assert args["repo"] == "repo"
    assert args["sha"] == "release"
    assert args["since"].startswith("2026-04-20T08:00:00")
    assert args["until"].startswith("2026-04-20T10:00:00")


def test_run_empty_result_returns_zero_commits() -> None:
    fake_result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": "",
        "structured_content": [],
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["commits"] == []
    assert result["commits_count"] == 0
    # Window metadata is always populated even when there are no commits —
    # "0 commits in this window" is itself evidence the RCA step can cite.
    assert result["window"]["branch"] == "main"


def test_run_defensive_against_non_list_structured_content() -> None:
    # MCP has occasionally been observed to return a dict under
    # structured_content (e.g. when the upstream paginates differently). The
    # tool must never crash on that shape — it should surface an empty list.
    fake_result = {
        "is_error": False,
        "tool": "list_commits",
        "arguments": {},
        "text": "",
        "structured_content": {"unexpected": "shape"},
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )
    assert result["commits"] == []
    assert result["commits_count"] == 0


def test_run_passes_per_page_to_mcp() -> None:
    mock_config = MagicMock()
    captured: dict[str, object] = {}

    def _fake_call(config, name, arguments):  # noqa: ARG001
        captured["arguments"] = arguments
        return {"is_error": False, "text": "", "structured_content": [], "content": []}

    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", side_effect=_fake_call),
    ):
        get_git_deploy_timeline(
            owner="org",
            repo="repo",
            per_page=50,
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    # MCP / GitHub REST API spells this camelCase; the tool must translate.
    assert captured["arguments"]["perPage"] == 50


def test_run_clamps_per_page_to_api_maximum() -> None:
    # GitHub REST list_commits caps per_page at 100. If a caller asks for more
    # the request silently truncates upstream, and our commits_count would be
    # wrong. We enforce the ceiling explicitly.
    mock_config = MagicMock()
    captured: dict[str, object] = {}

    def _fake_call(config, name, arguments):  # noqa: ARG001
        captured["arguments"] = arguments
        return {"is_error": False, "text": "", "structured_content": [], "content": []}

    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", side_effect=_fake_call),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            per_page=500,
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    assert captured["arguments"]["perPage"] == MAX_PER_PAGE
    assert result["window"]["per_page"] == MAX_PER_PAGE


def test_run_flags_window_truncated_when_page_is_full() -> None:
    # When the MCP returns exactly per_page commits, we don't know whether
    # more exist in the window. The window.truncated flag warns the agent
    # it may be looking at partial data.
    full_page = [
        {
            "sha": f"{i:040x}",
            "html_url": "",
            "commit": {
                "author": {"name": "A", "date": "2026-04-20T09:00:00Z"},
                "committer": {"date": "2026-04-20T09:00:01Z"},
                "message": f"commit {i}",
            },
        }
        for i in range(5)
    ]
    fake_result = {
        "is_error": False,
        "text": "5 commits",
        "structured_content": full_page,
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            per_page=5,
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    assert result["commits_count"] == 5
    assert result["window"]["truncated"] is True


def test_run_flags_window_not_truncated_when_fewer_than_page() -> None:
    fake_result = {
        "is_error": False,
        "text": "",
        "structured_content": [
            {
                "sha": "abc",
                "html_url": "",
                "commit": {
                    "author": {"name": "A", "date": "2026-04-20T09:00:00Z"},
                    "committer": {"date": "2026-04-20T09:00:01Z"},
                    "message": "one commit",
                },
            }
        ],
        "content": [],
    }
    mock_config = MagicMock()
    with (
        patch("app.tools.GitHubSearchCodeTool.github_mcp_config_from_env", return_value=None),
        patch("app.tools.GitHubSearchCodeTool.build_github_mcp_config", return_value=mock_config),
        patch("app.tools.GitDeployTimelineTool.call_github_mcp_tool", return_value=fake_result),
    ):
        result = get_git_deploy_timeline(
            owner="org",
            repo="repo",
            per_page=30,
            github_url="http://mcp",
            github_mode="streamable-http",
            github_token="tok",
        )

    assert result["commits_count"] == 1
    assert result["window"]["truncated"] is False


# ---------------------------------------------------------------------------
# _resolve_window
# ---------------------------------------------------------------------------


def test_resolve_window_defaults_to_default_minutes() -> None:
    since, until = _resolve_window("", "", None)
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    span = until_dt - since_dt
    assert span == timedelta(minutes=DEFAULT_WINDOW_MINUTES)


def test_resolve_window_honours_explicit_since_and_until() -> None:
    since, until = _resolve_window("2026-04-20T08:00:00Z", "2026-04-20T09:30:00Z", None)
    assert since == "2026-04-20T08:00:00Z"
    assert until == "2026-04-20T09:30:00Z"


def test_resolve_window_honours_window_minutes_when_since_missing() -> None:
    since, until = _resolve_window("", "2026-04-20T12:00:00Z", 30)
    assert since == "2026-04-20T11:30:00Z"
    assert until == "2026-04-20T12:00:00Z"


def test_resolve_window_clamps_to_max_span() -> None:
    # Caller asks for 60 days — we only allow MAX_WINDOW_MINUTES.
    since, until = _resolve_window("", "2026-04-20T00:00:00Z", 60 * 24 * 60)
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    assert (until_dt - since_dt) == timedelta(minutes=MAX_WINDOW_MINUTES)


def test_resolve_window_treats_malformed_since_as_missing() -> None:
    # Garbage 'since' falls through to the default-window branch rather than raising.
    since, until = _resolve_window("not-a-date", "", None)
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    assert until_dt - since_dt == timedelta(minutes=DEFAULT_WINDOW_MINUTES)


def test_resolve_window_rejects_inverted_range() -> None:
    # since > until is a caller mistake. Rather than pass an impossible range to
    # MCP, the helper discards the bad 'since' and falls back to the window
    # minutes branch anchored at the (still-valid) until.
    since, until = _resolve_window("2026-04-20T12:00:00Z", "2026-04-20T10:00:00Z", 30)
    assert until == "2026-04-20T10:00:00Z"
    assert since == "2026-04-20T09:30:00Z"  # until - 30 min


def test_resolve_window_zero_or_negative_minutes_falls_back_to_default() -> None:
    # Defensive: a caller might pass 0 or a negative window. Treat as unset.
    for minutes in (0, -5):
        since, until = _resolve_window("", "2026-04-20T10:00:00Z", minutes)
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        assert until_dt - since_dt == timedelta(minutes=DEFAULT_WINDOW_MINUTES)


def test_resolve_window_normalises_naive_timestamps_to_utc() -> None:
    # Naive input (no timezone) must be treated as UTC. Without this, the
    # subsequent astimezone(UTC) or > comparison against 'now' would raise
    # on one input but not the other.
    since, until = _resolve_window("2026-04-20T08:00:00", "2026-04-20T09:00:00", None)
    assert since == "2026-04-20T08:00:00Z"
    assert until == "2026-04-20T09:00:00Z"


def test_resolve_window_handles_mixed_aware_and_naive() -> None:
    # since has offset, until is naive — must not raise a TypeError on
    # the inverted-range comparison.
    since, until = _resolve_window("2026-04-20T08:00:00+00:00", "2026-04-20T09:00:00", None)
    assert since == "2026-04-20T08:00:00Z"
    assert until == "2026-04-20T09:00:00Z"


# ---------------------------------------------------------------------------
# _summarize_commit
# ---------------------------------------------------------------------------


def test_summarize_commit_keeps_subject_only() -> None:
    summarised = _summarize_commit(
        {
            "sha": "1234567890abcdef",
            "html_url": "u",
            "commit": {
                "author": {"name": "A", "date": "2026-04-20T09:00:00Z"},
                "committer": {"date": "2026-04-20T09:00:10Z"},
                "message": "subject line\n\nbody one\nbody two",
            },
        }
    )
    assert summarised["short_sha"] == "1234567"
    assert summarised["message_subject"] == "subject line"


def test_summarize_commit_handles_missing_fields() -> None:
    # Defensive: real MCP responses have been observed to drop optional fields
    # (e.g. committer, html_url); the summariser must never raise.
    summarised = _summarize_commit({"sha": "", "commit": {}})
    assert summarised["message_subject"] == ""
    assert summarised["author_name"] == ""
    assert summarised["short_sha"] == ""
