"""Tests for PostgreSQLCurrentQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostgreSQLCurrentQueriesTool import get_postgresql_current_queries
from tests.tools.conftest import BaseToolContract


class TestPostgreSQLCurrentQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_current_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_current_queries.__opensre_registered_tool__
    assert rt.name == "get_postgresql_current_queries"
    assert rt.source == "postgresql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "threshold_seconds": 2,
        "total_queries": 3,
        "queries": [
            {
                "pid": 12345,
                "username": "app_user",
                "application_name": "myapp",
                "client_addr": "192.168.1.100",
                "state": "active",
                "query_start": "2024-01-15 10:30:00",
                "duration_seconds": 15,
                "wait_event_type": "",
                "wait_event": "",
                "query_truncated": "SELECT * FROM large_table WHERE id IN (SELECT...",
            },
            {
                "pid": 12346,
                "username": "analytics",
                "application_name": "report_generator",
                "client_addr": "local",
                "state": "active",
                "query_start": "2024-01-15 10:29:45",
                "duration_seconds": 30,
                "wait_event_type": "IO",
                "wait_event": "DataFileRead",
                "query_truncated": "SELECT COUNT(*) FROM events WHERE created_at...",
            },
        ],
    }
    with patch(
        "app.tools.PostgreSQLCurrentQueriesTool.get_current_queries", return_value=fake_result
    ):
        result = get_postgresql_current_queries(
            host="localhost", database="testdb", threshold_seconds=2
        )
    assert result["threshold_seconds"] == 2
    assert result["total_queries"] == 3
    assert len(result["queries"]) == 2
    assert result["queries"][0]["duration_seconds"] == 15


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.PostgreSQLCurrentQueriesTool.get_current_queries",
        return_value={"source": "postgresql", "available": False, "error": "permission denied"},
    ):
        result = get_postgresql_current_queries(host="invalid", database="testdb")
    assert "error" in result
    assert result["available"] is False
