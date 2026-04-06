from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools.PrefectFlowRunsTool import PrefectFlowRunsTool


@pytest.fixture()
def tool() -> PrefectFlowRunsTool:
    return PrefectFlowRunsTool()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


def test_is_available_when_connection_verified(tool: PrefectFlowRunsTool) -> None:
    assert tool.is_available({"prefect": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified(tool: PrefectFlowRunsTool) -> None:
    assert tool.is_available({"prefect": {}}) is False
    assert tool.is_available({}) is False


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------


def test_extract_params_maps_source_fields(tool: PrefectFlowRunsTool) -> None:
    params = tool.extract_params({
        "prefect": {
            "api_url": "http://localhost:4200/api",
            "api_key": "key_abc",
            "account_id": "acc_1",
            "workspace_id": "ws_1",
            "connection_verified": True,
        }
    })
    assert params["api_url"] == "http://localhost:4200/api"
    assert params["api_key"] == "key_abc"
    assert params["account_id"] == "acc_1"
    assert params["workspace_id"] == "ws_1"
    assert params["states"] == ["FAILED", "CRASHED"]
    assert params["limit"] == 20


# ---------------------------------------------------------------------------
# run — missing api_url
# ---------------------------------------------------------------------------


def test_run_returns_unavailable_without_api_url(tool: PrefectFlowRunsTool) -> None:
    result = tool.run(api_url="")
    assert result["available"] is False
    assert result["flow_runs"] == []
    assert result["failed_runs"] == []


def test_run_returns_unavailable_for_whitespace_only_api_url(tool: PrefectFlowRunsTool) -> None:
    result = tool.run(api_url="   ")
    assert result["available"] is False


# ---------------------------------------------------------------------------
# run — API failures
# ---------------------------------------------------------------------------


def test_run_returns_unavailable_on_api_failure(tool: PrefectFlowRunsTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": False,
        "error": "HTTP 401: unauthorized",
    }
    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is False
    assert "401" in result["error"]
    assert result["flow_runs"] == []


# ---------------------------------------------------------------------------
# run — happy path
# ---------------------------------------------------------------------------


def test_run_returns_failed_runs(tool: PrefectFlowRunsTool) -> None:
    flow_runs = [
        {"id": "run_1", "name": "etl-run-1", "state_type": "FAILED", "state_name": "Failed"},
        {"id": "run_2", "name": "etl-run-2", "state_type": "COMPLETED", "state_name": "Completed"},
        {"id": "run_3", "name": "etl-run-3", "state_type": "CRASHED", "state_name": "Crashed"},
    ]
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": True,
        "flow_runs": flow_runs,
        "total": 3,
    }

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["failed_runs"]) == 2
    ids = {r["id"] for r in result["failed_runs"]}
    assert "run_1" in ids
    assert "run_3" in ids
    assert "run_2" not in ids


def test_run_empty_flow_runs(tool: PrefectFlowRunsTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {
        "success": True,
        "flow_runs": [],
        "total": 0,
    }

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total"] == 0
    assert result["failed_runs"] == []


# ---------------------------------------------------------------------------
# run — log fetching
# ---------------------------------------------------------------------------


def test_run_fetches_logs_when_run_id_provided(tool: PrefectFlowRunsTool) -> None:
    logs = [
        {"timestamp": "2026-01-01T00:00:00Z", "level": "ERROR", "message": "Task crashed with exitcode 1"},
        {"timestamp": "2026-01-01T00:00:01Z", "level": "INFO", "message": "Flow run started"},
    ]
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": [], "total": 0}
    mock_client.get_flow_run_logs.return_value = {"success": True, "logs": logs, "total": 2}

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(
            api_url="http://localhost:4200/api",
            fetch_logs_for_run_id="run_1",
        )

    mock_client.get_flow_run_logs.assert_called_once_with(flow_run_id="run_1", limit=100)
    assert len(result["logs"]) == 2
    # Only the ERROR line contains an error keyword
    assert len(result["error_log_lines"]) == 1
    assert "exitcode" in result["error_log_lines"][0]["message"]


def test_run_no_logs_fetched_without_run_id(tool: PrefectFlowRunsTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_flow_runs.return_value = {"success": True, "flow_runs": [], "total": 0}

    with patch("app.tools.PrefectFlowRunsTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    mock_client.get_flow_run_logs.assert_not_called()
    assert result["logs"] == []
    assert result["error_log_lines"] == []


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


def test_metadata_is_valid(tool: PrefectFlowRunsTool) -> None:
    meta = tool.metadata()
    assert meta.name == "prefect_flow_runs"
    assert meta.source == "prefect"
    assert "required" in meta.input_schema
    assert "api_url" in meta.input_schema["required"]
