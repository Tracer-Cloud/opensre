"""Tests for AzureSQLWaitStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.AzureSQLWaitStatsTool import get_azure_sql_wait_stats
from tests.tools.conftest import BaseToolContract


class TestAzureSQLWaitStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_azure_sql_wait_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_azure_sql_wait_stats.__opensre_registered_tool__
    assert rt.name == "get_azure_sql_wait_stats"
    assert rt.source == "azure_sql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "azure_sql",
        "available": True,
        "total_wait_types": 1,
        "waits": [
            {
                "wait_type": "PAGEIOLATCH_SH",
                "waiting_tasks_count": 1500,
                "wait_time_ms": 25000,
                "max_wait_time_ms": 500,
                "signal_wait_time_ms": 100,
            }
        ],
    }
    with patch(
        "app.tools.AzureSQLWaitStatsTool.get_wait_stats",
        return_value=fake_result,
    ):
        result = get_azure_sql_wait_stats(
            server="myserver.database.windows.net", database="testdb"
        )
    assert result["total_wait_types"] == 1
    assert result["waits"][0]["wait_type"] == "PAGEIOLATCH_SH"


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.AzureSQLWaitStatsTool.get_wait_stats",
        return_value={"source": "azure_sql", "available": False, "error": "timeout"},
    ):
        result = get_azure_sql_wait_stats(server="invalid", database="testdb")
    assert result["available"] is False
