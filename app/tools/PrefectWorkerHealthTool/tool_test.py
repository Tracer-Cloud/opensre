from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.tools.PrefectWorkerHealthTool import PrefectWorkerHealthTool


@pytest.fixture()
def tool() -> PrefectWorkerHealthTool:
    return PrefectWorkerHealthTool()


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


def test_is_available_when_connection_verified(tool: PrefectWorkerHealthTool) -> None:
    assert tool.is_available({"prefect": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified(tool: PrefectWorkerHealthTool) -> None:
    assert tool.is_available({"prefect": {}}) is False
    assert tool.is_available({}) is False


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------


def test_extract_params_maps_source_fields(tool: PrefectWorkerHealthTool) -> None:
    params = tool.extract_params({
        "prefect": {
            "api_url": "http://localhost:4200/api",
            "api_key": "",
            "account_id": "",
            "workspace_id": "",
            "work_pool_name": "my-pool",
            "connection_verified": True,
        }
    })
    assert params["api_url"] == "http://localhost:4200/api"
    assert params["work_pool_name"] == "my-pool"
    assert params["pool_limit"] == 20
    assert params["worker_limit"] == 20


# ---------------------------------------------------------------------------
# run — missing api_url
# ---------------------------------------------------------------------------


def test_run_returns_unavailable_without_api_url(tool: PrefectWorkerHealthTool) -> None:
    result = tool.run(api_url="")
    assert result["available"] is False
    assert result["work_pools"] == []
    assert result["workers"] == []


def test_run_returns_unavailable_for_whitespace_only_api_url(tool: PrefectWorkerHealthTool) -> None:
    result = tool.run(api_url="   ")
    assert result["available"] is False


# ---------------------------------------------------------------------------
# run — API failures
# ---------------------------------------------------------------------------


def test_run_returns_unavailable_on_api_failure(tool: PrefectWorkerHealthTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {
        "success": False,
        "error": "HTTP 403: forbidden",
    }

    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is False
    assert "403" in result["error"]
    assert result["work_pools"] == []


# ---------------------------------------------------------------------------
# run — work pools
# ---------------------------------------------------------------------------


def test_run_identifies_unhealthy_pools(tool: PrefectWorkerHealthTool) -> None:
    pools = [
        {"id": "pool_1", "name": "default", "status": "READY", "is_paused": False},
        {"id": "pool_2", "name": "batch", "status": "NOT_READY", "is_paused": False},
        {"id": "pool_3", "name": "nightly", "status": "READY", "is_paused": True},
    ]
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {
        "success": True,
        "work_pools": pools,
        "total": 3,
    }

    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total_pools"] == 3
    assert result["total_unhealthy_pools"] == 2
    names = {p["name"] for p in result["unhealthy_pools"]}
    assert "batch" in names
    assert "nightly" in names
    assert "default" not in names


def test_run_empty_work_pools(tool: PrefectWorkerHealthTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {
        "success": True,
        "work_pools": [],
        "total": 0,
    }

    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    assert result["available"] is True
    assert result["total_pools"] == 0
    assert result["unhealthy_pools"] == []


# ---------------------------------------------------------------------------
# run — workers
# ---------------------------------------------------------------------------


def test_run_fetches_workers_when_pool_name_provided(tool: PrefectWorkerHealthTool) -> None:
    workers = [
        {"name": "worker-1", "status": "ONLINE", "last_heartbeat_time": "2026-04-05T10:00:00Z"},
        {"name": "worker-2", "status": "OFFLINE", "last_heartbeat_time": "2026-04-04T08:00:00Z"},
        {"name": "worker-3", "status": "UNHEALTHY", "last_heartbeat_time": "2026-04-05T09:00:00Z"},
    ]
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {"success": True, "work_pools": [], "total": 0}
    mock_client.get_workers.return_value = {"success": True, "workers": workers, "total": 3}

    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(
            api_url="http://localhost:4200/api",
            work_pool_name="default",
        )

    mock_client.get_workers.assert_called_once_with(work_pool_name="default", limit=20)
    assert result["total_workers"] == 3
    assert result["total_unhealthy_workers"] == 2
    names = {w["name"] for w in result["unhealthy_workers"]}
    assert "worker-2" in names
    assert "worker-3" in names
    assert "worker-1" not in names


def test_run_skips_worker_fetch_without_pool_name(tool: PrefectWorkerHealthTool) -> None:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get_work_pools.return_value = {"success": True, "work_pools": [], "total": 0}

    with patch("app.tools.PrefectWorkerHealthTool.make_prefect_client", return_value=mock_client):
        result = tool.run(api_url="http://localhost:4200/api")

    mock_client.get_workers.assert_not_called()
    assert result["workers"] == []
    assert result["unhealthy_workers"] == []


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


def test_metadata_is_valid(tool: PrefectWorkerHealthTool) -> None:
    meta = tool.metadata()
    assert meta.name == "prefect_worker_health"
    assert meta.source == "prefect"
    assert "required" in meta.input_schema
    assert "api_url" in meta.input_schema["required"]
