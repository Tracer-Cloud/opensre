"""Tests for MySQLSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MySQLSlowQueriesTool import get_mysql_slow_queries
from tests.tools.conftest import BaseToolContract


class TestMySQLSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mysql_slow_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mysql_slow_queries.__opensre_registered_tool__
    assert rt.name == "get_mysql_slow_queries"
    assert rt.source == "mysql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "threshold_ms": 500.0,
        "total_queries": 2,
        "queries": [
            {
                "digest": "abc123",
                "query_truncated": "SELECT * FROM orders WHERE status = ? AND created_at > ?",
                "calls": 1250,
                "avg_time_ms": 850.123,
                "total_time_ms": 1062653.75,
                "min_time_ms": 120.456,
                "max_time_ms": 4500.789,
                "rows_examined": 125000,
                "rows_sent": 1250,
                "full_scans": 0,
            },
            {
                "digest": "def456",
                "query_truncated": "UPDATE inventory SET quantity = quantity - ? WHERE product_id = ?",
                "calls": 5430,
                "avg_time_ms": 620.5,
                "total_time_ms": 3369315.0,
                "min_time_ms": 200.1,
                "max_time_ms": 3200.9,
                "rows_examined": 5430,
                "rows_sent": 5430,
                "full_scans": 0,
            },
        ],
    }
    with patch("app.tools.MySQLSlowQueriesTool.get_slow_queries", return_value=fake_result):
        result = get_mysql_slow_queries(host="localhost", database="testdb", threshold_ms=500.0)
    assert result["threshold_ms"] == 500.0
    assert result["total_queries"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["avg_time_ms"] == 850.123
    assert result["queries"][1]["calls"] == 5430


def test_run_performance_schema_disabled() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "note": "performance_schema is not enabled. Enable it in my.cnf with performance_schema=ON.",
        "queries": [],
    }
    with patch("app.tools.MySQLSlowQueriesTool.get_slow_queries", return_value=fake_result):
        result = get_mysql_slow_queries(host="localhost", database="testdb")
    assert "note" in result
    assert len(result["queries"]) == 0


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MySQLSlowQueriesTool.get_slow_queries",
        return_value={"source": "mysql", "available": False, "error": "database does not exist"},
    ):
        result = get_mysql_slow_queries(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False
