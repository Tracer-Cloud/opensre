"""Tests for MySQLTableStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MySQLTableStatsTool import get_mysql_table_stats
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
    with patch("app.tools.MySQLTableStatsTool.get_table_stats", return_value=fake_result):
        result = get_mysql_table_stats(host="localhost", database="application_db")
    assert result["database"] == "application_db"
    assert result["total_tables"] == 3
    assert len(result["tables"]) == 3
    assert result["tables"][0]["table_name"] == "orders"
    assert result["tables"][0]["total_mb"] == 640.0
    assert result["tables"][1]["row_count"] == 50000


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MySQLTableStatsTool.get_table_stats",
        return_value={"source": "mysql", "available": False, "error": "unknown database 'invalid_db'"},
    ):
        result = get_mysql_table_stats(host="localhost", database="invalid_db")
    assert "error" in result
    assert result["available"] is False
