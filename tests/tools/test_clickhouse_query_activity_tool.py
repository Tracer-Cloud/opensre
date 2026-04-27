"""Tests for ClickHouseQueryActivityTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.ClickHouseQueryActivityTool import get_clickhouse_query_activity
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestClickHouseQueryActivityToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_clickhouse_query_activity.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    assert rt.name == "get_clickhouse_query_activity"
    assert rt.source == "clickhouse"
    assert isinstance(rt.surfaces, tuple)
    assert "investigation" in rt.surfaces
    assert "chat" in rt.surfaces


def test_is_available_with_connection_verified() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
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
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
    assert rt.is_available({"clickhouse": {"connection_verified": False}}) is False
    assert rt.is_available({"clickhouse": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
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
    rt = get_clickhouse_query_activity.__opensre_registered_tool__
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
        "total_returned": 2,
        "queries": [
            {
                "query_id": "qid-1",
                "type": "QueryFinish",
                "query": "SELECT count() FROM users",
                "duration_ms": 150,
                "read_rows": 1000000,
                "read_bytes": 5242880,
                "result_rows": 1,
                "memory_usage": 4096,
                "event_time": "2024-01-15 10:30:00",
            },
            {
                "query_id": "qid-2",
                "type": "QueryFinish",
                "query": "SELECT * FROM events LIMIT 10",
                "duration_ms": 45,
                "read_rows": 5000,
                "read_bytes": 102400,
                "result_rows": 10,
                "memory_usage": 2048,
                "event_time": "2024-01-15 10:31:00",
            },
        ],
    }
    with patch(
        "app.tools.ClickHouseQueryActivityTool.get_query_activity", return_value=fake_result
    ):
        result = get_clickhouse_query_activity(
            host="localhost",
            port=8123,
            database="default",
            username="default",
            password="",
            secure=False,
            limit=20,
        )
    assert result["available"] is True
    assert result["source"] == "clickhouse"
    assert result["total_returned"] == 2
    assert len(result["queries"]) == 2
    assert result["queries"][0]["query_id"] == "qid-1"
    assert result["queries"][0]["duration_ms"] == 150


def test_run_error_path() -> None:
    error_result = {
        "source": "clickhouse",
        "available": False,
        "error": "Connection refused",
    }
    with patch(
        "app.tools.ClickHouseQueryActivityTool.get_query_activity", return_value=error_result
    ):
        result = get_clickhouse_query_activity(host="localhost")
    assert result["available"] is False
    assert result["error"] == "Connection refused"


def test_run_with_custom_limit() -> None:
    fake_result = {
        "source": "clickhouse",
        "available": True,
        "total_returned": 5,
        "queries": [],
    }
    with patch(
        "app.tools.ClickHouseQueryActivityTool.get_query_activity", return_value=fake_result
    ) as mock_get:
        result = get_clickhouse_query_activity(host="localhost", limit=5)
    assert result["available"] is True
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert call_args.kwargs["limit"] == 5
