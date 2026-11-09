"""Tests for MongoDBReplicaStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mongodb.tools.mongodb_replica_status_tool import (
    _map_get_mongodb_replica_status,
    get_mongodb_replica_status,
)
from tests.tools.conftest import BaseToolContract


class TestMongoDBReplicaStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_replica_status.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_replica_status.__opensre_registered_tool__
    assert rt.name == "get_mongodb_replica_status"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {
        "set": "rs0",
        "members": [{"name": "rs0:27017", "stateStr": "PRIMARY", "health": 1}],
    }
    with patch(
        "integrations.mongodb.tools.mongodb_replica_status_tool.get_rs_status",
        return_value=fake_result,
    ):
        result = get_mongodb_replica_status(connection_string="mongodb://localhost:27017")
    assert "members" in result
    assert result["set"] == "rs0"


def test_run_error_propagated() -> None:
    with patch(
        "integrations.mongodb.tools.mongodb_replica_status_tool.get_rs_status",
        return_value={"error": "not a replica set"},
    ):
        result = get_mongodb_replica_status(connection_string="mongodb://localhost:27017")
    assert "error" in result


class TestMapGetMongodbReplicaStatus:
    def test_records_entry_with_unhealthy_member(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_replica_status(
            evidence,
            {
                "available": True,
                "set_name": "rs0",
                "members": [
                    {"name": "rs0-a:27017", "state": "PRIMARY", "health": 1},
                    {"name": "rs0-b:27017", "state": "(not reachable)", "health": 0},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mongodb_replica_status"
        assert entries[0]["summary"] == "2 member(s) in replica set 'rs0', unhealthy: rs0-b:27017"

    def test_records_entry_without_unhealthy_clause_when_all_healthy(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_replica_status(
            evidence,
            {
                "available": True,
                "set_name": "rs0",
                "members": [{"name": "rs0-a:27017", "state": "PRIMARY", "health": 1}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 member(s) in replica set 'rs0'"

    def test_records_note_when_not_part_of_replica_set(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_replica_status(
            evidence,
            {
                "available": True,
                "set_name": "",
                "members": [],
                "note": "Server is not part of a replica set.",
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "Server is not part of a replica set."

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_replica_status(evidence, {"available": False, "error": "auth failed"}, {})

        assert "catalog_entries" not in evidence
