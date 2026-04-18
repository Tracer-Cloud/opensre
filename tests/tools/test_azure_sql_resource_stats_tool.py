"""Tests for AzureSQLResourceStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.AzureSQLResourceStatsTool import get_azure_sql_resource_stats
from tests.tools.conftest import BaseToolContract


class TestAzureSQLResourceStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_azure_sql_resource_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_azure_sql_resource_stats.__opensre_registered_tool__
    assert rt.name == "get_azure_sql_resource_stats"
    assert rt.source == "azure_sql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "azure_sql",
        "available": True,
        "window_minutes": 30,
        "total_samples": 2,
        "throttling_risk": "moderate",
        "samples": [
            {
                "end_time": "2024-01-01 12:00:00",
                "avg_cpu_percent": 65.0,
                "avg_data_io_percent": 20.0,
            },
        ],
    }
    with patch(
        "app.tools.AzureSQLResourceStatsTool.get_resource_stats",
        return_value=fake_result,
    ):
        result = get_azure_sql_resource_stats(
            server="myserver.database.windows.net", database="testdb"
        )
    assert result["throttling_risk"] == "moderate"
    assert result["total_samples"] == 2


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.AzureSQLResourceStatsTool.get_resource_stats",
        return_value={"source": "azure_sql", "available": False, "error": "timeout"},
    ):
        result = get_azure_sql_resource_stats(server="invalid", database="testdb")
    assert result["available"] is False
