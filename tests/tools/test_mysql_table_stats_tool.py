"""Tests for MySQLTableStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mysql.tools.mysql_table_stats_tool import (
    _map_get_mysql_table_stats,
    get_mysql_table_stats,
)
from tests.tools.conftest import BaseToolContract


class TestMySQLTableStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mysql_table_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mysql_table_stats.__opensre_registered_tool__
    assert rt.name == "get_mysql_table_stats"
    assert rt.source == "mysql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mysql",
        "available": True,
        "database": "application_db",
        "total_tables": 3,
        "tables": [
            {
                "table_name": "orders",
                "row_count": 2500000,
                "data_mb": 512.0,
                "index_mb": 128.0,
                "total_mb": 640.0,
                "engine": "InnoDB",
            },
            {
                "table_name": "users",
                "row_count": 50000,
                "data_mb": 8.0,
                "index_mb": 2.5,
                "total_mb": 10.5,
                "engine": "InnoDB",
            },
            {
                "table_name": "sessions",
                "row_count": 150000,
                "data_mb": 24.0,
                "index_mb": 6.0,
                "total_mb": 30.0,
                "engine": "InnoDB",
            },
        ],
    }
    with patch(
        "integrations.mysql.tools.mysql_table_stats_tool.get_table_stats", return_value=fake_result
    ):
        result = get_mysql_table_stats(host="localhost", database="application_db")
    assert result["database"] == "application_db"
    assert result["total_tables"] == 3
    assert len(result["tables"]) == 3
    assert result["tables"][0]["table_name"] == "orders"
    assert result["tables"][0]["total_mb"] == 640.0
    assert result["tables"][1]["row_count"] == 50000


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mysql.tools.mysql_table_stats_tool.get_table_stats",
        return_value={
            "source": "mysql",
            "available": False,
            "error": "unknown database 'invalid_db'",
        },
    ):
        result = get_mysql_table_stats(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False


class TestMapGetMysqlTableStats:
    def test_records_entry_with_largest_table(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_table_stats(
            evidence,
            {
                "available": True,
                "database": "application_db",
                "total_tables": 2,
                "tables": [
                    {"table_name": "orders", "size": {"total_mb": 640.0}},
                    {"table_name": "users", "size": {"total_mb": 10.5}},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mysql_table_stats"
        assert (
            entries[0]["summary"] == "2 table(s) in 'application_db', largest 'orders' at 640.0MB"
        )

    def test_records_nothing_when_no_tables(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_table_stats(
            evidence, {"available": True, "database": "empty_db", "tables": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mysql_table_stats(evidence, {"available": False, "error": "unknown database"}, {})

        assert "catalog_entries" not in evidence
