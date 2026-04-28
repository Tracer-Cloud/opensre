"""Tests for GitLabMRsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GitLabMRsTool import list_gitlab_mrs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitLabMRsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitlab_mrs.__opensre_registered_tool__


def test_is_available_requires_connection_and_project_id() -> None:
    rt = list_gitlab_mrs.__opensre_registered_tool__
    assert rt.is_available({"gitlab": {"connection_verified": True, "project_id": "42"}}) is True
    assert rt.is_available({"gitlab": {"connection_verified": True}}) is False
    assert rt.is_available({"gitlab": {"project_id": "42"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_gitlab_mrs.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "updated_after": "2026-01-01T00:00:00Z",
                "target_branch": "release",
                "gitlab_url": "https://gitlab.example.com",
                "gitlab_token": "glpat-test",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["project_id"] == "42"
    assert params["updated_after"] == "2026-01-01T00:00:00Z"
    assert params["target_branch"] == "release"
    assert params["per_page"] == 10
    assert params["gitlab_url"] == "https://gitlab.example.com"
    assert params["gitlab_token"] == "glpat-test"


def test_extract_params_defaults_target_branch_to_main() -> None:
    rt = list_gitlab_mrs.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "updated_after": "2026-01-01T00:00:00Z",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["target_branch"] == "main"


def test_extract_params_defaults_updated_after_to_empty_string() -> None:
    rt = list_gitlab_mrs.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["updated_after"] == ""


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch("app.tools.GitLabMRsTool._resolve_config", return_value=None):
        result = list_gitlab_mrs(project_id="42")
    assert result["available"] is False
    assert "not configured" in result["error"]
    assert result["mrs"] == []


def test_run_happy_path_returns_mrs() -> None:
    fake_mrs = [
        {"iid": 1, "title": "fix: deploy bug"},
        {"iid": 2, "title": "feat: new endpoint"},
    ]
    with (
        patch("app.tools.GitLabMRsTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabMRsTool.get_gitlab_mrs", return_value=fake_mrs) as mock_fn,
    ):
        result = list_gitlab_mrs(
            project_id="42",
            target_branch="main",
            updated_after="2026-01-01T00:00:00Z",
            per_page=10,
        )
    assert result["available"] is True
    assert result["source"] == "gitlab"
    assert result["mrs"] == fake_mrs
    mock_fn.assert_called_once()


def test_run_error_path_returns_empty_mrs_when_integration_returns_empty() -> None:
    with (
        patch("app.tools.GitLabMRsTool._resolve_config", return_value=MagicMock()),
        patch("app.tools.GitLabMRsTool.get_gitlab_mrs", return_value=[]),
    ):
        result = list_gitlab_mrs(project_id="42")
    assert result["available"] is True
    assert result["mrs"] == []
