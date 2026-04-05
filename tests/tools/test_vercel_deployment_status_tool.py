from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools.VercelDeploymentStatusTool import VercelDeploymentStatusTool


@pytest.fixture()
def tool() -> VercelDeploymentStatusTool:
    return VercelDeploymentStatusTool()


def test_is_available_when_connection_verified(tool: VercelDeploymentStatusTool) -> None:
    assert tool.is_available({"vercel": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified(tool: VercelDeploymentStatusTool) -> None:
    assert tool.is_available({"vercel": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_source_fields(tool: VercelDeploymentStatusTool) -> None:
    params = tool.extract_params({
        "vercel": {
            "api_token": "tok_abc",
            "team_id": "team_1",
            "project_id": "proj_frontend",
            "connection_verified": True,
        }
    })
    assert params["api_token"] == "tok_abc"
    assert params["team_id"] == "team_1"
    assert params["project_id"] == "proj_frontend"
    assert params["limit"] == 10


def test_run_returns_failed_deployments_for_error_state(tool: VercelDeploymentStatusTool) -> None:
    deployments = [
        {"id": "dpl_1", "state": "ERROR", "error": "Build failed", "meta": {}},
        {"id": "dpl_2", "state": "READY", "error": "", "meta": {}},
        {"id": "dpl_3", "state": "CANCELED", "error": "", "meta": {}},
    ]
    mock_client = MagicMock()
    mock_client.list_deployments.return_value = {
        "success": True, "deployments": deployments, "total": 3
    }
    with patch("app.tools.VercelDeploymentStatusTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test")

    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["failed_deployments"]) == 2
    ids = {d["id"] for d in result["failed_deployments"]}
    assert "dpl_1" in ids
    assert "dpl_3" in ids
    assert "dpl_2" not in ids


def test_run_empty_deployments_list(tool: VercelDeploymentStatusTool) -> None:
    mock_client = MagicMock()
    mock_client.list_deployments.return_value = {
        "success": True, "deployments": [], "total": 0
    }
    with patch("app.tools.VercelDeploymentStatusTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test")

    assert result["available"] is True
    assert result["total"] == 0
    assert result["failed_deployments"] == []


def test_run_returns_unavailable_on_api_failure(tool: VercelDeploymentStatusTool) -> None:
    mock_client = MagicMock()
    mock_client.list_deployments.return_value = {
        "success": False, "error": "HTTP 401: unauthorized"
    }
    with patch("app.tools.VercelDeploymentStatusTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test")

    assert result["available"] is False
    assert "401" in result["error"]
    assert result["deployments"] == []


def test_run_returns_unavailable_without_token(tool: VercelDeploymentStatusTool) -> None:
    result = tool.run(api_token="")
    assert result["available"] is False
    assert result["deployments"] == []
    assert result["failed_deployments"] == []


def test_run_returns_unavailable_for_whitespace_only_token(tool: VercelDeploymentStatusTool) -> None:
    result = tool.run(api_token="   ")
    assert result["available"] is False
    assert result["deployments"] == []


def test_run_passes_project_id_and_state_to_client(tool: VercelDeploymentStatusTool) -> None:
    mock_client = MagicMock()
    mock_client.list_deployments.return_value = {
        "success": True, "deployments": [], "total": 0
    }
    with patch("app.tools.VercelDeploymentStatusTool.make_vercel_client", return_value=mock_client):
        tool.run(api_token="tok_test", project_id="proj_1", state="ERROR", limit=5)

    mock_client.list_deployments.assert_called_once_with(
        project_id="proj_1", limit=5, state="ERROR"
    )


def test_metadata_is_valid(tool: VercelDeploymentStatusTool) -> None:
    meta = tool.metadata()
    assert meta.name == "vercel_deployment_status"
    assert meta.source == "vercel"
    assert "required" in meta.input_schema
    assert "api_token" in meta.input_schema["required"]
