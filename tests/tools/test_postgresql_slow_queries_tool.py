"""Tests for PostgreSQLSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostgreSQLSlowQueriesTool import get_postgresql_slow_queries
from tests.tools.conftest import BaseToolContract


class TestPostgreSQLSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_slow_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_slow_queries.__opensre_registered_tool__
    assert rt.name == "get_postgresql_slow_queries"
    assert rt.source == "postgresql"


def test_run_happy_path_with_extension() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "extension_available": True,
        "threshold_ms": 500,
        "total_queries": 2,
        "queries": [
            {
                "queryid": "1234567890123456789",
                "query_truncated": "SELECT * FROM large_table lt JOIN other_table ot ON lt.id = ot.large_id WHERE lt.created_at > $1 AND...",
                "calls": 145,
                "total_time_ms": 72500,
                "mean_time_ms": 500,
                "min_time_ms": 200,
                "max_time_ms": 2500,
                "stddev_time_ms": 150,
                "total_rows": 14500,
                "cache_hit_percent": 85.2,
            },
            {
                "queryid": "9876543210987654321",
                "query_truncated": "UPDATE users SET last_login = $1, login_count = login_count + 1 WHERE id = $2",
                "calls": 1023,
                "total_time_ms": 1534500,
                "mean_time_ms": 1500,
                "min_time_ms": 800,
                "max_time_ms": 5000,
                "stddev_time_ms": 250,
                "total_rows": 1023,
                "cache_hit_percent": 99.1,
            },
        ],
    }
    with patch("app.tools.PostgreSQLSlowQueriesTool.get_slow_queries", return_value=fake_result):
        result = get_postgresql_slow_queries(host="localhost", database="testdb", threshold_ms=500)
    assert result["extension_available"] is True
    assert result["threshold_ms"] == 500
    assert result["total_queries"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["mean_time_ms"] == 500
    assert result["queries"][1]["cache_hit_percent"] == 99.1


def test_run_extension_not_available() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "extension_available": False,
        "note": (
            "pg_stat_statements extension is not installed. "
            "Install it with CREATE EXTENSION pg_stat_statements; "
            "and add 'pg_stat_statements' to shared_preload_libraries."
        ),
        "queries": [],
    }
    with patch("app.tools.PostgreSQLSlowQueriesTool.get_slow_queries", return_value=fake_result):
        result = get_postgresql_slow_queries(host="localhost", database="testdb")
    assert result["extension_available"] is False
    assert "note" in result
    assert len(result["queries"]) == 0


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.PostgreSQLSlowQueriesTool.get_slow_queries",
        return_value={
            "source": "postgresql",
            "available": False,
            "error": "database does not exist",
        },
    ):
        result = get_postgresql_slow_queries(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False
