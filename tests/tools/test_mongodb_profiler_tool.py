"""Tests for MongoDBProfilerTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBProfilerTool import get_mongodb_profiler_data
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
    with patch("app.tools.MongoDBProfilerTool.get_profiler_data", return_value=fake_result):
        result = get_mongodb_profiler_data(
            connection_string="mongodb://localhost:27017",
            database="my-db",
            threshold_ms=100,
        )
    assert "queries" in result


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBProfilerTool.get_profiler_data",
        return_value={"error": "profiling not enabled"},
    ):
        result = get_mongodb_profiler_data(connection_string="mongodb://localhost", database="mydb")
    assert "error" in result
