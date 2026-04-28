"""Tests for ClickHouseQueryActivityTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.ClickHouseQueryActivityTool import get_clickhouse_query_activity
from tests.tools.conftest import BaseToolContract


class TestClickHouseQueryActivityToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_clickhouse_query_activity.__opensre_registered_tool__


def test_is_available_true_when_connection_verified() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    assert (
        rt.is_available({"clickhouse": {"host": "ch.example.com", "connection_verified": True}})
        is True
    )


def test_is_available_false_without_connection_verified() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    assert rt.is_available({"clickhouse": {"host": "ch.example.com"}}) is False


def test_is_available_false_when_no_clickhouse_source() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    sources = {
        "clickhouse": {
            "host": "ch.example.com",
            "port": 9000,
            "database": "analytics",
            "username": "admin",
            "password": "secret",
            "secure": True,
            "connection_verified": True,
        }
    }
    params = rt.extract_params(sources)
    assert params["host"] == "ch.example.com"
    assert params["port"] == 9000
    assert params["database"] == "analytics"
    assert params["username"] == "admin"
    assert params["password"] == "secret"
    assert params["secure"] is True


def test_extract_params_uses_defaults_for_missing_fields() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    params = rt.extract_params({"clickhouse": {"host": "ch.example.com"}})
    assert params["port"] == 8123
    assert params["database"] == "default"
    assert params["username"] == "default"
    assert params["password"] == ""
    assert params["secure"] is False


def test_run_happy_path() -> None:
    mock_result = {
        "source": "clickhouse",
        "available": True,
        "total_returned": 2,
        "queries": [
            {"query_id": "q1", "query": "SELECT 1", "duration_ms": 10},
            {"query_id": "q2", "query": "SELECT sleep(1)", "duration_ms": 1000},
        ],
    }
    with patch(
        "app.tools.ClickHouseQueryActivityTool.get_query_activity", return_value=mock_result
    ):
        result = get_clickhouse_query_activity(host="ch.example.com", limit=20)
    assert result["available"] is True
    assert result["total_returned"] == 2
    assert len(result["queries"]) == 2


def test_run_error_path() -> None:
    error_result = {
        "source": "clickhouse",
        "available": False,
        "error": "connection refused",
    }
    with patch(
        "app.tools.ClickHouseQueryActivityTool.get_query_activity", return_value=error_result
    ):
        result = get_clickhouse_query_activity(host="ch.example.com")
    assert result["available"] is False
    assert "error" in result
