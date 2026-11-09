"""Tests for GitLabPipelinesTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from integrations.gitlab.tools.gitlab_pipelines_tool import (
    _map_list_gitlab_pipelines,
    list_gitlab_pipelines,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGitLabPipelinesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_gitlab_pipelines.__opensre_registered_tool__


def test_is_available_requires_connection_and_project_id() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__
    assert rt.is_available({"gitlab": {"connection_verified": True, "project_id": "42"}}) is True
    assert rt.is_available({"gitlab": {"connection_verified": True}}) is False
    assert rt.is_available({"gitlab": {"project_id": "42"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "gitlab": {
                "connection_verified": True,
                "project_id": "42",
                "updated_after": "2026-01-01T00:00:00Z",
                "ref_name": "release",
                "gitlab_url": "https://gitlab.example.com",
                "gitlab_token": "glpat-test",
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["project_id"] == "42"
    assert params["updated_after"] == "2026-01-01T00:00:00Z"
    assert params["ref"] == "release"
    assert params["status"] == "failed"
    assert params["per_page"] == 10
    assert params["gitlab_url"] == "https://gitlab.example.com"
    assert params["gitlab_token"] == "glpat-test"


def test_extract_params_maps_local_store_credentials() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__
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


def test_extract_params_defaults_ref_to_main() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__
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
    assert params["ref"] == "main"


def test_extract_params_defaults_updated_after_to_empty_string() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__
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


def test_schema_does_not_expose_gitlab_credentials_as_model_inputs() -> None:
    rt = list_gitlab_pipelines.__opensre_registered_tool__

    assert "gitlab_url" not in rt.input_schema["properties"]
    assert "gitlab_token" not in rt.input_schema["properties"]


def test_run_returns_unavailable_when_config_missing() -> None:
    with patch(
        "integrations.gitlab.tools.gitlab_pipelines_tool._resolve_config", return_value=None
    ):
        result = list_gitlab_pipelines(project_id="42")
    assert result["available"] is False
    assert "not configured" in result["error"]
    assert result["pipelines"] == []


def test_run_happy_path_returns_pipelines() -> None:
    fake_pipelines = [
        {"id": 100, "status": "failed", "ref": "main"},
        {"id": 101, "status": "failed", "ref": "main"},
    ]
    with (
        patch(
            "integrations.gitlab.tools.gitlab_pipelines_tool._resolve_config",
            return_value=MagicMock(),
        ),
        patch(
            "integrations.gitlab.tools.gitlab_pipelines_tool.get_gitlab_pipelines",
            return_value=fake_pipelines,
        ) as mock_fn,
    ):
        result = list_gitlab_pipelines(
            project_id="42",
            ref="main",
            updated_after="2026-01-01T00:00:00Z",
            status="failed",
            per_page=10,
        )
    assert result["available"] is True
    assert result["source"] == "gitlab"
    assert result["pipelines"] == fake_pipelines
    mock_fn.assert_called_once()


def test_run_error_path_returns_empty_pipelines_when_integration_returns_empty() -> None:
    with (
        patch(
            "integrations.gitlab.tools.gitlab_pipelines_tool._resolve_config",
            return_value=MagicMock(),
        ),
        patch(
            "integrations.gitlab.tools.gitlab_pipelines_tool.get_gitlab_pipelines", return_value=[]
        ),
    ):
        result = list_gitlab_pipelines(project_id="42")
    assert result["available"] is True
    assert result["pipelines"] == []


class TestMapListGitlabPipelines:
    def test_records_plain_count_with_status_filter(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_pipelines(
            evidence,
            {"available": True, "pipelines": [{"id": 1}, {"id": 2}]},
            {"per_page": 10, "status": "failed"},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "list_gitlab_pipelines"
        assert entries[0]["summary"] == "2 pipeline(s) with status 'failed'"

    def test_qualifies_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_pipelines(
            evidence,
            {"available": True, "pipelines": [{"id": i} for i in range(10)]},
            {"per_page": 10, "status": "failed"},
        )

        assert evidence["catalog_entries"][0]["summary"] == "10+ pipeline(s) with status 'failed'"

    def test_records_nothing_when_no_pipelines(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_pipelines(evidence, {"available": True, "pipelines": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_gitlab_pipelines(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
