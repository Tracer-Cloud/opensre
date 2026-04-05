"""Tests for PrefectWorkerHealthTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.PrefectWorkerHealthTool import PrefectWorkerHealthTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestPrefectWorkerHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return PrefectWorkerHealthTool()


def test_is_available_requires_connection_verified() -> None:
    tool = PrefectWorkerHealthTool()
    assert tool.is_available({"prefect": {"connection_verified": True}}) is True
    assert tool.is_available({"prefect": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = PrefectWorkerHealthTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["api_url"] == "http://localhost:4200/api"


def test_run_returns_unavailable_when_no_api_url() -> None:
    tool = PrefectWorkerHealthTool()
    result = tool.run(api_url="")
    assert result["available"] is False


def test_run_returns_unavailable_when_client_none() -> None:
    tool = PrefectWorkerHealthTool()
    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=None):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is False


def test_run_happy_path_no_work_pool() -> None:
    tool = PrefectWorkerHealthTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {
        "success": True,
        "work_pools": [
            {"name": "pool-1", "status": "READY", "is_paused": False},
            {"name": "pool-2", "status": "PAUSED", "is_paused": True},
        ],
    }
    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")
    assert result["available"] is True
    assert result["total_pools"] == 2
    assert len(result["unhealthy_pools"]) == 1


def test_run_with_work_pool_name() -> None:
    tool = PrefectWorkerHealthTool()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {"success": True, "work_pools": []}
    mock_client.get_workers.return_value = {
        "success": True,
        "workers": [
            {"name": "worker-1", "status": "ONLINE"},
            {"name": "worker-2", "status": "OFFLINE"},
        ],
    }
    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api", work_pool_name="pool-1")
    assert result["total_workers"] == 2
    assert len(result["unhealthy_workers"]) == 1
