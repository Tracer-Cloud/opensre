"""Tests for MongoDBCurrentOpsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBCurrentOpsTool import get_mongodb_current_ops
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
    with patch("app.tools.MongoDBCurrentOpsTool.get_current_ops", return_value=fake_result):
        result = get_mongodb_current_ops(
            connection_string="mongodb://localhost:27017",
            threshold_ms=1000,
        )
    assert "ops" in result


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBCurrentOpsTool.get_current_ops", return_value={"error": "auth failed"}
    ):
        result = get_mongodb_current_ops(connection_string="mongodb://invalid")
    assert "error" in result
