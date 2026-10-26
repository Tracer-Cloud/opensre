"""Tests for MySQLSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mysql.tools.mysql_slow_queries_tool import (
    _map_get_mysql_slow_queries,
    get_mysql_slow_queries,
)
from tests.tools.conftest import BaseToolContract


class TestMySQLSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
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
    with patch(
        "integrations.mysql.tools.mysql_slow_queries_tool.get_slow_queries",
        return_value=fake_result,
    ):
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
    with patch(
        "integrations.mysql.tools.mysql_slow_queries_tool.get_slow_queries",
        return_value=fake_result,
    ):
        result = get_mysql_slow_queries(host="localhost", database="testdb")
    assert "note" in result
    assert len(result["queries"]) == 0


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mysql.tools.mysql_slow_queries_tool.get_slow_queries",
        return_value={"source": "mysql", "available": False, "error": "database does not exist"},
    ):
        result = get_mysql_slow_queries(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False


class TestMapGetMysqlSlowQueries:
    def test_records_entry_with_slowest_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_slow_queries(
            evidence,
            {
                "available": True,
                "performance_schema_available": True,
                "threshold_ms": 500.0,
                "total_queries": 2,
                "queries": [
                    {"digest_text": "SELECT * FROM orders", "avg_time_ms": 850.1},
                    {"digest_text": "UPDATE inventory", "avg_time_ms": 620.5},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mysql_slow_queries"
        assert entries[0]["summary"] == "2 queries above 500.0ms, slowest avg 850.1ms"

    def test_records_note_when_performance_schema_disabled(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_slow_queries(
            evidence,
            {
                "available": True,
                "performance_schema_available": False,
                "note": "performance_schema is disabled. Enable it in my.cnf to collect slow query data.",
                "queries": [],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "performance_schema is disabled. Enable it in my.cnf to collect slow query data."
        )

    def test_records_nothing_when_no_slow_queries_found(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_slow_queries(
            evidence,
            {"available": True, "performance_schema_available": True, "queries": []},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_slow_queries(evidence, {"available": False, "error": "timeout"}, {})

        assert "catalog_entries" not in evidence
