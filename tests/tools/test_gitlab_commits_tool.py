"""Tests for GitLabCommitsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GitLabCommitsTool import list_gitlab_commits
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitLabCommitsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitlab_commits.__opensre_registered_tool__


def test_is_available_requires_connection_and_project_id() -> None:
    rt = list_gitlab_commits.__opensre_registered_tool__
    assert rt.is_available({"gitlab": {"connection_verified": True, "project_id": "42"}}) is True
    assert rt.is_available({"gitlab": {"connection_verified": True}}) is False
    assert rt.is_available({"gitlab": {"project_id": "42"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_gitlab_commits.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "ref_name": "develop",
                "since": "2026-01-01T00:00:00Z",
                "gitlab_url": "https://gitlab.example.com",
                "gitlab_token": "glpat-test",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["project_id"] == "42"
    assert params["ref_name"] == "develop"
    assert params["since"] == "2026-01-01T00:00:00Z"
    assert params["per_page"] == 10
    assert params["gitlab_url"] == "https://gitlab.example.com"
    assert params["gitlab_token"] == "glpat-test"


def test_extract_params_defaults_ref_name_to_main() -> None:
    rt = list_gitlab_commits.__opensre_registered_tool__
    sources = mock_agent_state({"gitlab": {"connection_verified": True, "project_id": "42"}})
    params = rt.extract_params(sources)
    assert params["ref_name"] == "main"


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch("app.tools.GitLabCommitsTool._resolve_config", return_value=None):
        result = list_gitlab_commits(project_id="42")
    assert result["available"] is False
    assert "not configured" in result["error"]
    assert result["commits"] == []


def test_run_happy_path_returns_commits() -> None:
    fake_commits = [
        {"id": "abc", "title": "fix: bug"},
        {"id": "def", "title": "feat: thing"},
    ]
    with (
        patch("app.tools.GitLabCommitsTool._resolve_config", return_value=MagicMock()),
        patch(
            "app.tools.GitLabCommitsTool.get_gitlab_commits", return_value=fake_commits
        ) as mock_fn,
    ):
        result = list_gitlab_commits(
            project_id="42",
            ref_name="main",
            since="2026-01-01T00:00:00Z",
            per_page=10,
        )
    assert result["available"] is True
    assert result["source"] == "gitlab"
    assert result["commits"] == fake_commits
    mock_fn.assert_called_once()


def test_run_error_path_returns_empty_commits_when_integration_returns_empty() -> None:
    """The integration coerces non-list (e.g. error) responses to []; the tool
    should still return a valid available payload with no commits."""
    with (
        patch("app.tools.GitLabCommitsTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabCommitsTool.get_gitlab_commits", return_value=[]),
    ):
        result = list_gitlab_commits(project_id="42")
    assert result["available"] is True
    assert result["commits"] == []
