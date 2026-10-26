"""Tests for ClickHouseQueryActivityTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.clickhouse.tools.clickhouse_query_activity_tool import (
    _map_get_clickhouse_query_activity,
    get_clickhouse_query_activity,
)
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
        "integrations.clickhouse.tools.clickhouse_query_activity_tool.get_query_activity",
        return_value=mock_result,
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
        "integrations.clickhouse.tools.clickhouse_query_activity_tool.get_query_activity",
        return_value=error_result,
    ):
        result = get_clickhouse_query_activity(host="ch.example.com")
    assert result["available"] is False
    assert "error" in result


class TestMapGetClickhouseQueryActivity:
    def test_records_entry_with_failed_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_clickhouse_query_activity(
            evidence,
            {
                "available": True,
                "total_returned": 3,
                "queries": [
                    {"query_id": "1", "type": "QueryFinish"},
                    {"query_id": "2", "type": "QueryFinish"},
                    {"query_id": "3", "type": "ExceptionWhileProcessing"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_clickhouse_query_activity"
        assert entries[0]["summary"] == "3 queries, 1 failed"

    def test_records_entry_without_failed_suffix_when_all_succeeded(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_clickhouse_query_activity(
            evidence,
            {"available": True, "queries": [{"query_id": "1", "type": "QueryFinish"}]},
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 query"

    def test_records_nothing_when_no_queries(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_clickhouse_query_activity(
            evidence, {"available": True, "total_returned": 0, "queries": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_clickhouse_query_activity(
            evidence, {"available": False, "error": "Not configured."}, {}
        )

        assert "catalog_entries" not in evidence
