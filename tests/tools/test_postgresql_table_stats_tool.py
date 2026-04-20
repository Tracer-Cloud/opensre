"""Tests for PostgreSQLTableStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.PostgreSQLTableStatsTool import get_postgresql_table_stats
from tests.tools.conftest import BaseToolContract


class TestPostgreSQLTableStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_postgresql_table_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_postgresql_table_stats.__opensre_registered_tool__
    assert rt.name == "get_postgresql_table_stats"
    assert rt.source == "postgresql"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "schema": "public",
        "total_tables": 3,
        "tables": [
            {
                "schema": "public",
                "table_name": "events",
                "tuples": {
                    "inserted": 1500000,
                    "updated": 75000,
                    "deleted": 25000,
                    "live": 1450000,
                    "dead": 0,
                },
                "scans": {
                    "sequential": 25,
                    "sequential_tuples": 362500,
                    "index": 15420,
                    "index_tuples": 1450000,
                    "index_usage_percent": 99.8,
                },
                "maintenance": {
                    "last_vacuum": "2024-01-15 09:30:00",
                    "last_autovacuum": "2024-01-15 10:15:30",
                    "last_analyze": "2024-01-15 09:45:00",
                    "last_autoanalyze": "2024-01-15 10:20:15",
                },
                "size": {
                    "total_bytes": 536870912,
                    "table_bytes": 402653184,
                    "indexes_bytes": 134217728,
                    "total_mb": 512.0,
                },
            },
            {
                "schema": "public",
                "table_name": "users",
                "tuples": {
                    "inserted": 50000,
                    "updated": 125000,
                    "deleted": 2000,
                    "live": 48000,
                    "dead": 15,
                },
                "scans": {
                    "sequential": 150,
                    "sequential_tuples": 7200000,
                    "index": 3500,
                    "index_tuples": 168000,
                    "index_usage_percent": 95.9,
                },
                "maintenance": {
                    "last_vacuum": None,
                    "last_autovacuum": "2024-01-15 08:45:00",
                    "last_analyze": None,
                    "last_autoanalyze": "2024-01-15 09:00:30",
                },
                "size": {
                    "total_bytes": 16777216,
                    "table_bytes": 12582912,
                    "indexes_bytes": 4194304,
                    "total_mb": 16.0,
                },
            },
            {
                "schema": "public",
                "table_name": "sessions",
                "tuples": {
                    "inserted": 2500000,
                    "updated": 500000,
                    "deleted": 2300000,
                    "live": 200000,
                    "dead": 1500,
                },
                "scans": {
                    "sequential": 5,
                    "sequential_tuples": 1000000,
                    "index": 8500,
                    "index_tuples": 1700000,
                    "index_usage_percent": 99.9,
                },
                "maintenance": {
                    "last_vacuum": "2024-01-15 06:00:00",
                    "last_autovacuum": "2024-01-15 10:30:00",
                    "last_analyze": "2024-01-15 06:15:00",
                    "last_autoanalyze": "2024-01-15 10:35:00",
                },
                "size": {
                    "total_bytes": 83886080,
                    "table_bytes": 50331648,
                    "indexes_bytes": 33554432,
                    "total_mb": 80.0,
                },
            },
        ],
    }
    with patch("app.tools.PostgreSQLTableStatsTool.get_table_stats", return_value=fake_result):
        result = get_postgresql_table_stats(
            host="localhost", database="testdb", schema_name="public"
        )
    assert result["schema"] == "public"
    assert result["total_tables"] == 3
    assert len(result["tables"]) == 3
    assert result["tables"][0]["table_name"] == "events"
    assert result["tables"][0]["size"]["total_mb"] == 512.0
    assert result["tables"][0]["scans"]["index_usage_percent"] == 99.8
    assert result["tables"][1]["maintenance"]["last_vacuum"] is None
    assert result["tables"][2]["tuples"]["live"] == 200000


def test_run_custom_schema() -> None:
    fake_result = {
        "source": "postgresql",
        "available": True,
        "schema": "analytics",
        "total_tables": 1,
        "tables": [
            {
                "schema": "analytics",
                "table_name": "reports",
                "tuples": {
                    "inserted": 10000,
                    "updated": 2000,
                    "deleted": 500,
                    "live": 9500,
                    "dead": 5,
                },
                "scans": {
                    "sequential": 10,
                    "sequential_tuples": 95000,
                    "index": 250,
                    "index_tuples": 23750,
                    "index_usage_percent": 96.2,
                },
                "maintenance": {
                    "last_vacuum": "2024-01-15 07:00:00",
                    "last_autovacuum": None,
                    "last_analyze": "2024-01-15 07:30:00",
                    "last_autoanalyze": "2024-01-15 08:00:00",
                },
                "size": {
                    "total_bytes": 1048576,
                    "table_bytes": 786432,
                    "indexes_bytes": 262144,
                    "total_mb": 1.0,
                },
            },
        ],
    }
    with patch("app.tools.PostgreSQLTableStatsTool.get_table_stats", return_value=fake_result):
        result = get_postgresql_table_stats(
            host="localhost", database="testdb", schema_name="analytics"
        )
    assert result["schema"] == "analytics"
    assert result["total_tables"] == 1
    assert result["tables"][0]["table_name"] == "reports"


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.PostgreSQLTableStatsTool.get_table_stats",
        return_value={
            "source": "postgresql",
            "available": False,
            "error": "relation 'public.nonexistent' does not exist",
        },
    ):
        result = get_postgresql_table_stats(host="localhost", database="testdb")
    assert "error" in result
    assert result["available"] is False
