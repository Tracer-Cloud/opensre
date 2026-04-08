from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools.VercelLogsTool import VercelLogsTool


@pytest.fixture()
def tool() -> VercelLogsTool:
    return VercelLogsTool()


def test_is_available_requires_only_connection_verified(tool: VercelLogsTool) -> None:
    # deployment_id is no longer required for is_available; it's checked inside run()
    assert tool.is_available({"vercel": {"connection_verified": True, "deployment_id": "dpl_1"}}) is True
    assert tool.is_available({"vercel": {"connection_verified": True}}) is True
    assert tool.is_available({"vercel": {"deployment_id": "dpl_1"}}) is False
    assert tool.is_available({"vercel": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_source_fields(tool: VercelLogsTool) -> None:
    params = tool.extract_params({
        "vercel": {
            "api_token": "tok_abc",
            "team_id": "team_1",
            "project_id": "prj_1",
            "deployment_id": "dpl_xyz",
            "connection_verified": True,
        }
    })
    assert params["api_token"] == "tok_abc"
    assert params["project_id"] == "prj_1"
    assert params["deployment_id"] == "dpl_xyz"
    assert params["include_runtime_logs"] is True


def test_run_returns_events_and_filters_error_events(tool: VercelLogsTool) -> None:
    events = [
        {"type": "stdout", "text": "Building dependencies...", "created": 1},
        {"type": "stderr", "text": "Error: cannot find module 'react'", "created": 2},
        {"type": "stdout", "text": "Build complete", "created": 3},
        {"type": "stderr", "text": "exception in handler", "created": 4},
    ]
    mock_client = MagicMock()
    mock_client.get_deployment.return_value = {"success": True, "deployment": {"id": "dpl_xyz", "state": "ERROR"}}
    mock_client.get_deployment_events.return_value = {"success": True, "events": events}
    mock_client.get_runtime_logs.return_value = {"success": True, "logs": []}

    with patch("app.tools.VercelLogsTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test", deployment_id="dpl_xyz")

    assert result["available"] is True
    assert len(result["events"]) == 4
    assert len(result["error_events"]) == 2
    error_texts = {e["text"] for e in result["error_events"]}
    assert any("Error" in t for t in error_texts)
    assert any("exception" in t for t in error_texts)


def test_run_skips_runtime_logs_when_disabled(tool: VercelLogsTool) -> None:
    mock_client = MagicMock()
    mock_client.get_deployment.return_value = {"success": True, "deployment": {}}
    mock_client.get_deployment_events.return_value = {"success": True, "events": []}

    with patch("app.tools.VercelLogsTool.make_vercel_client", return_value=mock_client):
        tool.run(api_token="tok_test", deployment_id="dpl_xyz", include_runtime_logs=False)

    mock_client.get_runtime_logs.assert_not_called()


def test_run_includes_runtime_logs_by_default(tool: VercelLogsTool) -> None:
    mock_client = MagicMock()
    mock_client.get_deployment.return_value = {"success": True, "deployment": {}}
    mock_client.get_deployment_events.return_value = {"success": True, "events": []}
    mock_client.get_runtime_logs.return_value = {
        "success": True,
        "logs": [{"id": "l1"}, {"id": "l2"}],
    }

    with patch("app.tools.VercelLogsTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test", deployment_id="dpl_xyz", project_id="prj_9")

    mock_client.get_runtime_logs.assert_called_once()
    assert mock_client.get_runtime_logs.call_args.kwargs.get("project_id") == "prj_9"
    assert result["total_runtime_logs"] == 2
    assert len(result["runtime_logs"]) == 2


def test_run_gracefully_handles_deployment_fetch_failure(tool: VercelLogsTool) -> None:
    mock_client = MagicMock()
    mock_client.get_deployment.return_value = {"success": False, "error": "not found"}
    mock_client.get_deployment_events.return_value = {"success": True, "events": []}
    mock_client.get_runtime_logs.return_value = {"success": True, "logs": []}

    with patch("app.tools.VercelLogsTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test", deployment_id="dpl_xyz")

    assert result["available"] is True
    assert result["deployment"] == {}


def test_run_gracefully_handles_events_fetch_failure(tool: VercelLogsTool) -> None:
    mock_client = MagicMock()
    mock_client.get_deployment.return_value = {"success": True, "deployment": {"id": "dpl_xyz"}}
    mock_client.get_deployment_events.return_value = {"success": False, "error": "rate limited"}
    mock_client.get_runtime_logs.return_value = {"success": True, "logs": []}

    with patch("app.tools.VercelLogsTool.make_vercel_client", return_value=mock_client):
        result = tool.run(api_token="tok_test", deployment_id="dpl_xyz")

    assert result["available"] is True
    assert result["events"] == []
    assert result["error_events"] == []


def test_run_returns_unavailable_without_token(tool: VercelLogsTool) -> None:
    result = tool.run(api_token="", deployment_id="dpl_xyz")
    assert result["available"] is False
    assert result["events"] == []
    assert result["runtime_logs"] == []


def test_run_returns_unavailable_for_whitespace_only_token(tool: VercelLogsTool) -> None:
    result = tool.run(api_token="\t  \n", deployment_id="dpl_xyz")
    assert result["available"] is False
    assert result["events"] == []


def test_run_returns_informative_error_without_deployment_id(tool: VercelLogsTool) -> None:
    result = tool.run(api_token="tok_test", deployment_id="")
    assert result["available"] is False
    assert "deployment_id" in result["error"]
    assert "vercel_deployment_status" in result["error"]


def test_metadata_requires_deployment_id(tool: VercelLogsTool) -> None:
    meta = tool.metadata()
    assert meta.name == "vercel_deployment_logs"
    assert meta.source == "vercel"
    assert "deployment_id" in meta.input_schema["required"]
    assert "api_token" in meta.input_schema["required"]
