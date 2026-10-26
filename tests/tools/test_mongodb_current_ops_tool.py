"""Tests for MongoDBCurrentOpsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mongodb.tools.mongodb_current_ops_tool import (
    _map_get_mongodb_current_ops,
    get_mongodb_current_ops,
)
from tests.tools.conftest import BaseToolContract


class TestMongoDBCurrentOpsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_current_ops.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_current_ops.__opensre_registered_tool__
    assert rt.name == "get_mongodb_current_ops"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {"ops": [{"opid": 1, "secs_running": 5000, "ns": "mydb.users"}]}
    with patch(
        "integrations.mongodb.tools.mongodb_current_ops_tool.get_current_ops",
        return_value=fake_result,
    ):
        result = get_mongodb_current_ops(
            connection_string="mongodb://localhost:27017",
            threshold_ms=1000,
        )
    assert "ops" in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mongodb.tools.mongodb_current_ops_tool.get_current_ops",
        return_value={"error": "auth failed"},
    ):
        result = get_mongodb_current_ops(connection_string="mongodb://invalid")
    assert "error" in result


class TestMapGetMongodbCurrentOps:
    def test_records_entry_with_longest_running(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_current_ops(
            evidence,
            {
                "available": True,
                "threshold_ms": 1000,
                "total_ops": 2,
                "operations": [
                    {"opid": 1, "secs_running": 5},
                    {"opid": 2, "secs_running": 12},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mongodb_current_ops"
        assert entries[0]["summary"] == "2 op(s) over 1000ms, longest running 12s"

    def test_records_nothing_when_no_operations(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_current_ops(
            evidence, {"available": True, "threshold_ms": 1000, "operations": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_current_ops(evidence, {"available": False, "error": "auth failed"}, {})

        assert "catalog_entries" not in evidence
