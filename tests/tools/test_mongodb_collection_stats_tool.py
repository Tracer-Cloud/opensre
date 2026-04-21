"""Tests for MongoDBCollectionStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBCollectionStatsTool import get_mongodb_collection_stats
from tests.tools.conftest import BaseToolContract


class TestMongoDBCollectionStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_collection_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_collection_stats.__opensre_registered_tool__
    assert rt.name == "get_mongodb_collection_stats"
    assert rt.source == "mongodb"


def test_run_happy_path() -> None:
    fake_result = {
        "collection": "my-collection",
        "count": 1000,
        "size": 2048,
        "indexes": [],
    }
    with patch(
        "app.tools.MongoDBCollectionStatsTool.get_collection_stats", return_value=fake_result
    ):
        result = get_mongodb_collection_stats(
            connection_string="mongodb://localhost:27017",
            database="my-db",
            collection="my-collection",
        )
    assert result["count"] == 1000


def test_run_error_propagated() -> None:
    fake_result = {"error": "Connection refused"}
    with patch(
        "app.tools.MongoDBCollectionStatsTool.get_collection_stats", return_value=fake_result
    ):
        result = get_mongodb_collection_stats(
            connection_string="mongodb://invalid",
            database="my-db",
            collection="my-collection",
        )
    assert "error" in result
