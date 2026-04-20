"""Tests for MongoDBServerStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBServerStatusTool import get_mongodb_server_status
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
    with patch("app.tools.MongoDBServerStatusTool.get_server_status", return_value=fake_result):
        result = get_mongodb_server_status(connection_string="mongodb://localhost:27017")
    assert result["version"] == "6.0.10"
    assert "connections" in result


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBServerStatusTool.get_server_status",
        return_value={"error": "connection timeout"},
    ):
        result = get_mongodb_server_status(connection_string="mongodb://invalid")
    assert "error" in result
