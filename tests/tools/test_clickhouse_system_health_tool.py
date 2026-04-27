"""Tests for ClickHouseSystemHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.ClickHouseSystemHealthTool import get_clickhouse_system_health
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestClickHouseSystemHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_clickhouse_system_health.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    assert rt.name == "get_clickhouse_system_health"
    assert rt.source == "clickhouse"
    assert isinstance(rt.surfaces, tuple)
    assert "investigation" in rt.surfaces
    assert "chat" in rt.surfaces


def test_is_available_with_connection_verified() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "clickhouse": {
                "connection_verified": True,
                "host": "localhost",
                "port": 8123,
                "database": "default",
                "username": "default",
                "password": "",
                "secure": False,
            }
        }
    )
    assert rt.is_available(sources) is True


def test_is_available_without_connection_verified() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    assert rt.is_available({"clickhouse": {"connection_verified": False}}) is False
    assert rt.is_available({"clickhouse": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    sources = mock_agent_state(
        {
            "clickhouse": {
                "connection_verified": True,
                "host": "clickhouse.example.com",
                "port": 8124,
                "database": "metrics",
                "username": "readonly",
                "password": "secret",
                "secure": True,
            }
        }
    )
    params = rt.extract_params(sources)
    assert params["host"] == "clickhouse.example.com"
    assert params["port"] == 8124
    assert params["database"] == "metrics"
    assert params["username"] == "readonly"
    assert params["password"] == "secret"
    assert params["secure"] is True


def test_extract_params_with_defaults() -> None:
    rt = get_clickhouse_system_health.__opensre_registered_tool__
    sources = mock_agent_state({"clickhouse": {"connection_verified": True, "host": "localhost"}})
    params = rt.extract_params(sources)
    assert params["host"] == "localhost"
    assert params["port"] == 8123
    assert params["database"] == "default"
    assert params["username"] == "default"
    assert params["secure"] is False


def test_run_happy_path() -> None:
    fake_result = {
        "source": "clickhouse",
        "available": True,
        "version": "24.3.3.102",
        "uptime_seconds": 864000,
        "metrics": {
            "Query": 5,
            "Merge": 0,
            "PartMutation": 0,
            "TCPConnection": 3,
            "HTTPConnection": 1,
        },
    }
    with patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=fake_result):
        result = get_clickhouse_system_health(
            host="localhost",
            port=8123,
            database="default",
            username="default",
            password="",
            secure=False,
            include_table_stats=False,
        )
    assert result["available"] is True
    assert result["source"] == "clickhouse"
    assert result["version"] == "24.3.3.102"
    assert result["uptime_seconds"] == 864000
    assert result["metrics"]["Query"] == 5


def test_run_happy_path_with_table_stats() -> None:
    health_result = {
        "source": "clickhouse",
        "available": True,
        "version": "24.3.3.102",
        "uptime_seconds": 864000,
        "metrics": {"Query": 5, "Merge": 0},
    }
    table_result = {
        "source": "clickhouse",
        "available": True,
        "database": "default",
        "total_tables": 2,
        "tables": [
            {
                "database": "default",
                "table": "users",
                "total_rows": 1000000,
                "total_bytes": 52428800,
                "part_count": 10,
                "last_modified": "2024-01-15 10:00:00",
            },
            {
                "database": "default",
                "table": "events",
                "total_rows": 5000000,
                "total_bytes": 209715200,
                "part_count": 20,
                "last_modified": "2024-01-15 09:00:00",
            },
        ],
    }
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch("app.tools.ClickHouseSystemHealthTool.get_table_stats", return_value=table_result),
    ):
        result = get_clickhouse_system_health(
            host="localhost",
            port=8123,
            database="default",
            username="default",
            password="",
            secure=False,
            include_table_stats=True,
        )
    assert result["available"] is True
    assert result["version"] == "24.3.3.102"
    assert "table_stats" in result
    assert len(result["table_stats"]) == 2
    assert result["table_stats"][0]["table"] == "users"


def test_run_no_table_stats_when_unavailable() -> None:
    health_result = {
        "source": "clickhouse",
        "available": False,
        "error": "Connection refused",
    }
    with patch(
        "app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result
    ):
        result = get_clickhouse_system_health(
            host="localhost",
            include_table_stats=True,
        )
    assert result["available"] is False
    assert result["error"] == "Connection refused"
    # Verify table_stats is not in result
    assert "table_stats" not in result


def test_run_no_table_stats_when_disabled() -> None:
    health_result = {
        "source": "clickhouse",
        "available": True,
        "version": "24.3.3.102",
        "uptime_seconds": 864000,
        "metrics": {},
    }
    with (
        patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=health_result),
        patch("app.tools.ClickHouseSystemHealthTool.get_table_stats") as mock_table,
    ):
        result = get_clickhouse_system_health(
            host="localhost",
            include_table_stats=False,
        )
    assert result["available"] is True
    # get_table_stats should NOT be called when include_table_stats=False
    mock_table.assert_not_called()
    assert "table_stats" not in result


def test_run_error_path() -> None:
    error_result = {
        "source": "clickhouse",
        "available": False,
        "error": "DNS resolution failed",
    }
    with patch("app.tools.ClickHouseSystemHealthTool.get_system_health", return_value=error_result):
        result = get_clickhouse_system_health(host="localhost")
    assert result["available"] is False
    assert result["error"] == "DNS resolution failed"
