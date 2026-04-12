"""Tests for MongoDBReplicaStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBReplicaStatusTool import get_mongodb_replica_status
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
    with patch("app.tools.MongoDBReplicaStatusTool.get_rs_status", return_value=fake_result):
        result = get_mongodb_replica_status(connection_string="mongodb://localhost:27017")
    assert "members" in result
    assert result["set"] == "rs0"


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBReplicaStatusTool.get_rs_status",
        return_value={"error": "not a replica set"},
    ):
        result = get_mongodb_replica_status(connection_string="mongodb://localhost:27017")
    assert "error" in result
