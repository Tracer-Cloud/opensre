"""Tests for AzureSQLServerStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.AzureSQLServerStatusTool import get_azure_sql_server_status
from tests.tools.conftest import BaseToolContract


class TestAzureSQLServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_azure_sql_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_azure_sql_server_status.__opensre_registered_tool__
    assert rt.name == "get_azure_sql_server_status"
    assert rt.source == "azure_sql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "azure_sql",
        "available": True,
        "version": "Microsoft SQL Azure (RTM) - 12.0.2000.8",
        "service_tier": {
            "edition": "Standard",
            "service_objective": "S1",
            "elastic_pool": None,
        },
        "connections": {"total": 25, "active": 15, "idle": 10},
        "resource_utilization": {
            "avg_cpu_percent": 45.2,
            "avg_data_io_percent": 12.1,
            "avg_log_write_percent": 5.3,
            "avg_memory_usage_percent": 30.0,
            "max_worker_percent": 8.5,
            "max_session_percent": 6.2,
            "sample_time": "2024-01-01 12:00:00",
        },
        "database_size_mb": 512.0,
    }
    with patch(
        "app.tools.AzureSQLServerStatusTool.get_server_status",
        return_value=fake_result,
    ):
        result = get_azure_sql_server_status(
            server="myserver.database.windows.net", database="testdb"
        )
    assert result["version"] == "Microsoft SQL Azure (RTM) - 12.0.2000.8"
    assert result["connections"]["total"] == 25
    assert result["resource_utilization"]["avg_cpu_percent"] == 45.2


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.AzureSQLServerStatusTool.get_server_status",
        return_value={"source": "azure_sql", "available": False, "error": "connection timeout"},
    ):
        result = get_azure_sql_server_status(server="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
