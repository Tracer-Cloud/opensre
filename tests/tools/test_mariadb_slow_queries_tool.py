"""Tests for MariaDBSlowQueriesTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mariadb.tools.mariadb_slow_queries_tool import (
    _map_get_mariadb_slow_queries,
    get_mariadb_slow_queries,
)
from tests.tools.conftest import BaseToolContract


class TestMariaDBSlowQueriesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mariadb_slow_queries.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mariadb_slow_queries.__opensre_registered_tool__
    assert rt.name == "get_mariadb_slow_queries"
    assert rt.source == "mariadb"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mariadb",
        "available": True,
        "total_queries": 1,
        "queries": [{"digest_text": "SELECT ...", "count": 100, "avg_time_ms": 50.5}],
    }
    with patch(
        "integrations.mariadb.tools.mariadb_slow_queries_tool.get_slow_queries",
        return_value=fake_result,
    ):
        result = get_mariadb_slow_queries(host="localhost", database="test", username="user")
    assert result["available"] is True
    assert result["total_queries"] == 1


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mariadb.tools.mariadb_slow_queries_tool.get_slow_queries",
        return_value={"source": "mariadb", "available": False, "error": "connection timeout"},
    ):
        result = get_mariadb_slow_queries(host="invalid", database="test", username="user")
    assert "error" in result


class TestMapGetMariadbSlowQueries:
    def test_records_entry_with_slowest_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_slow_queries(
            evidence,
            {
                "available": True,
                "total_queries": 2,
                "queries": [
                    {"digest_text": "SELECT * FROM orders", "avg_time_ms": 90.5},
                    {"digest_text": "UPDATE inventory", "avg_time_ms": 40.2},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mariadb_slow_queries"
        assert entries[0]["summary"] == "2 queries surveyed, slowest avg 90.5ms"

    def test_records_note_when_performance_schema_disabled(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_slow_queries(
            evidence,
            {
                "available": True,
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

        _map_get_mariadb_slow_queries(evidence, {"available": True, "queries": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mariadb_slow_queries(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
