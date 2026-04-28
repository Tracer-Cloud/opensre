"""Tests for ClickHouseSystemHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.ClickHouseSystemHealthTool import get_clickhouse_system_health
from tests.tools.conftest import BaseToolContract


class TestClickHouseSystemHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_clickhouse_system_health.__opensre_registered_tool__


def test_is_available_true_when_connection_verified() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    assert (
        rt.is_available({"clickhouse": {"host": "ch.example.com", "connection_verified": True}})
        is True
    )


def test_is_available_false_without_connection_verified() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    assert rt.is_available({"clickhouse": {"host": "ch.example.com"}}) is False


def test_is_available_false_when_no_clickhouse_source() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    sources = {
        "clickhouse": {
            "host": "ch.example.com",
            "port": 9000,
            "database": "analytics",
            "username": "admin",
            "password": "secret",
            "secure": False,
            "connection_verified": True,
        }
    }
    params = rt.extract_params(sources)
    assert params["host"] == "ch.example.com"
    assert params["port"] == 9000
    assert params["database"] == "analytics"
    assert params["username"] == "admin"
    assert params["password"] == "secret"
    assert params["secure"] is False


def test_extract_params_uses_defaults_for_missing_fields() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    params = rt.extract_params({"clickhouse": {"host": "ch.example.com"}})
    assert params["port"] == 8123
    assert params["database"] == "default"
    assert params["username"] == "default"
    assert params["password"] == ""
    assert params["secure"] is False


def test_run_happy_path_with_table_stats() -> None:
    health_result = {
        "source": "clickhouse",
        "available": True,
        "version": "23.8.1",
        "uptime_seconds": 3600,
        "metrics": {"Query": 5, "TCPConnection": 10},
    }
    table_result = {
        "source": "clickhouse",
        "available": True,
        "tables": [
            {"table": "events", "total_rows": 1000, "total_bytes": 512000},
        ],
    }
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch("app.tools.ClickHouseSystemHealthTool.get_table_stats", return_value=table_result),
    ):
        result = get_clickhouse_system_health(host="ch.example.com", include_table_stats=True)
    assert result["available"] is True
    assert result["version"] == "23.8.1"
    assert result["table_stats"] == table_result["tables"]


def test_run_happy_path_without_table_stats() -> None:
    health_result = {
        "source": "clickhouse",
        "available": True,
        "version": "23.8.1",
        "uptime_seconds": 3600,
        "metrics": {},
    }
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch("app.tools.ClickHouseSystemHealthTool.get_table_stats") as mock_table_stats,
    ):
        result = get_clickhouse_system_health(host="ch.example.com", include_table_stats=False)
    assert result["available"] is True
    assert "table_stats" not in result
    mock_table_stats.assert_not_called()


def test_run_table_stats_error_falls_back_to_empty_list() -> None:
    health_result = {
        "source": "clickhouse",
        "available": True,
        "version": "23.8.1",
        "uptime_seconds": 3600,
        "metrics": {},
    }
    table_error_result = {"source": "clickhouse", "available": False, "error": "timeout"}
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch(
            "app.tools.ClickHouseSystemHealthTool.get_table_stats", return_value=table_error_result
        ),
    ):
        result = get_clickhouse_system_health(host="ch.example.com", include_table_stats=True)
    assert result["available"] is True
    assert result["table_stats"] == []


def test_run_skips_table_stats_when_health_unavailable() -> None:
    health_result = {
        "source": "clickhouse",
        "available": False,
        "error": "connection refused",
    }
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch("app.tools.ClickHouseSystemHealthTool.get_table_stats") as mock_table_stats,
    ):
        result = get_clickhouse_system_health(host="ch.example.com", include_table_stats=True)
    assert result["available"] is False
    mock_table_stats.assert_not_called()


def test_run_error_path() -> None:
    error_result = {
        "source": "clickhouse",
        "available": False,
        "error": "connection refused",
    }
    with patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=error_result):
        result = get_clickhouse_system_health(host="ch.example.com")
    assert result["available"] is False
    assert "error" in result
