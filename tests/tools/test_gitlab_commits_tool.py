"""Tests for GitLabCommitsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from integrations.gitlab.tools.gitlab_commits_tool import (
    _map_list_gitlab_commits,
    _resolve_config,
    list_gitlab_commits,
)
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


def test_extract_params_maps_local_store_credentials() -> None:
    """Store-configured integrations carry base_url/auth_token (not the legacy
    gitlab_url/gitlab_token keys); extract_params must still surface them."""
    rt = list_gitlab_commits.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "base_url": "https://gitlab.example.com/api/v4",
                "auth_token": "glpat-store",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["gitlab_url"] == "https://gitlab.example.com/api/v4"
    assert params["gitlab_token"] == "glpat-store"


def test_extract_params_defaults_ref_name_to_main() -> None:
    rt = list_gitlab_commits.__opensre_registered_tool__
    sources = mock_agent_state({"gitlab": {"connection_verified": True, "project_id": "42"}})
    params = rt.extract_params(sources)
    assert params["ref_name"] == "main"


def test_schema_does_not_expose_gitlab_credentials_as_model_inputs() -> None:
    rt = list_gitlab_commits.__opensre_registered_tool__

    assert "gitlab_url" not in rt.input_schema["properties"]
    assert "gitlab_token" not in rt.input_schema["properties"]


def test_resolve_config_uses_env_config_when_no_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("GITLAB_BASE_URL", "https://env.gitlab.example.com/api/v4")

    config = _resolve_config(None, None)

    assert config is not None
    assert config.auth_token == "env-token"
    assert config.api_base_url == "https://env.gitlab.example.com/api/v4"


def test_resolve_config_rejects_url_override_without_matching_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("GITLAB_BASE_URL", "https://env.gitlab.example.com/api/v4")

    assert _resolve_config("https://source.gitlab.example.com", "") is None


def test_resolve_config_prefers_injected_store_creds_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both env and the resolved integration store carry credentials, the
    store (injected) credentials win — env is only a fallback."""
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("GITLAB_BASE_URL", "https://env.gitlab.example.com/api/v4")

    config = _resolve_config("https://store.gitlab.example.com/api/v4", "store-token")

    assert config is not None
    assert config.auth_token == "store-token"
    assert config.api_base_url == "https://store.gitlab.example.com/api/v4"


def test_run_uses_resolved_sources_creds_when_env_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: a store/keyring-configured GitLab integration with NO env vars
    still resolves credentials from the resolved sources dict and succeeds."""
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)

    rt = list_gitlab_commits.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "base_url": "https://gitlab.example.com/api/v4",
                "auth_token": "glpat-store",
            }
        }
    )
    params = rt.extract_params(sources)

    fake_commits = [{"id": "abc", "title": "fix: bug"}]
    with patch(
        "integrations.gitlab.tools.gitlab_commits_tool.get_gitlab_commits",
        return_value=fake_commits,
    ) as mock_fn:
        result = list_gitlab_commits(**params)

    assert result["available"] is True
    assert result["commits"] == fake_commits
    config = mock_fn.call_args.kwargs["config"]
    assert config.auth_token == "glpat-store"
    assert config.api_base_url == "https://gitlab.example.com/api/v4"


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch("integrations.gitlab.tools.gitlab_commits_tool._resolve_config", return_value=None):
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
        patch(
            "integrations.gitlab.tools.gitlab_commits_tool._resolve_config",
            return_value=MagicMock(),
        ),
        patch(
            "integrations.gitlab.tools.gitlab_commits_tool.get_gitlab_commits",
            return_value=fake_commits,
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
        patch(
            "integrations.gitlab.tools.gitlab_commits_tool._resolve_config",
            return_value=MagicMock(),
        ),
        patch("integrations.gitlab.tools.gitlab_commits_tool.get_gitlab_commits", return_value=[]),
    ):
        result = list_gitlab_commits(project_id="42")
    assert result["available"] is True
    assert result["commits"] == []


class TestMapListGitlabCommits:
    def test_records_plain_count_when_under_page_size(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_commits(
            evidence,
            {"available": True, "commits": [{"id": "abc"}, {"id": "def"}]},
            {"per_page": 10},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "list_gitlab_commits"
        assert entries[0]["summary"] == "2 commit(s)"

    def test_qualifies_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_commits(
            evidence,
            {"available": True, "commits": [{"id": str(i)} for i in range(10)]},
            {"per_page": 10},
        )

        assert evidence["catalog_entries"][0]["summary"] == "10+ commit(s)"

    def test_records_nothing_when_no_commits(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_commits(evidence, {"available": True, "commits": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_commits(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
