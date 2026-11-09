"""Tests for MongoDBProfilerTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mongodb.tools.mongodb_profiler_tool import (
    _map_get_mongodb_profiler_data,
    get_mongodb_profiler_data,
)
from tests.tools.conftest import BaseToolContract


class TestMongoDBProfilerToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_profiler_data.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_profiler_data.__opensre_registered_tool__
    assert rt.name == "get_mongodb_profiler_data"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {"queries": [{"op": "query", "millis": 500, "ns": "mydb.users"}]}
    with patch(
        "integrations.mongodb.tools.mongodb_profiler_tool.get_profiler_data",
        return_value=fake_result,
    ):
        result = get_mongodb_profiler_data(
            connection_string="mongodb://localhost:27017",
            database="my-db",
            threshold_ms=100,
        )
    assert "queries" in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mongodb.tools.mongodb_profiler_tool.get_profiler_data",
        return_value={"error": "profiling not enabled"},
    ):
        result = get_mongodb_profiler_data(connection_string="mongodb://localhost", database="mydb")
    assert "error" in result


class TestMapGetMongodbProfilerData:
    def test_records_entry_with_slowest_query(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_profiler_data(
            evidence,
            {
                "available": True,
                "profiling_level": 1,
                "threshold_ms": 100,
                "total_entries": 2,
                "entries": [
                    {"op": "query", "millis": 500, "ns": "mydb.users"},
                    {"op": "update", "millis": 1200, "ns": "mydb.orders"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mongodb_profiler_data"
        assert entries[0]["summary"] == "2 slow queries shown above 100ms, slowest 1200ms"

    def test_records_note_when_profiling_disabled(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_profiler_data(
            evidence,
            {
                "available": True,
                "profiling_level": 0,
                "note": "Profiling is disabled on this database.",
                "entries": [],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"] == "Profiling is disabled on this database."
        )

    def test_records_nothing_when_no_slow_queries_found(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_profiler_data(
            evidence, {"available": True, "profiling_level": 1, "entries": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_profiler_data(
            evidence, {"available": False, "error": "profiling not enabled"}, {}
        )

        assert "catalog_entries" not in evidence
