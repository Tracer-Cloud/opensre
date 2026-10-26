"""Tests for MongoDBServerStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mongodb.tools.mongodb_server_status_tool import (
    _map_get_mongodb_server_status,
    get_mongodb_server_status,
)
from tests.tools.conftest import BaseToolContract


class TestMongoDBServerStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_server_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_server_status.__opensre_registered_tool__
    assert rt.name == "get_mongodb_server_status"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {
        "version": "6.0.10",
        "connections": {"current": 10, "available": 990},
        "mem": {"resident": 512, "virtual": 2048},
    }
    with patch(
        "integrations.mongodb.tools.mongodb_server_status_tool.get_server_status",
        return_value=fake_result,
    ):
        result = get_mongodb_server_status(connection_string="mongodb://localhost:27017")
    assert result["version"] == "6.0.10"
    assert "connections" in result


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mongodb.tools.mongodb_server_status_tool.get_server_status",
        return_value={"error": "connection timeout"},
    ):
        result = get_mongodb_server_status(connection_string="mongodb://invalid")
    assert "error" in result


class TestMapGetMongodbServerStatus:
    def test_records_entry_with_connection_counts(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_server_status(
            evidence,
            {
                "available": True,
                "version": "6.0.10",
                "connections": {"current": 10, "available": 990},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mongodb_server_status"
        assert entries[0]["summary"] == "MongoDB 6.0.10, 10 connection(s) in use, 990 available"

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_server_status(
            evidence, {"available": False, "error": "connection timeout"}, {}
        )

        assert "catalog_entries" not in evidence
