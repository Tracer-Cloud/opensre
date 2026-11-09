"""Tests for MongoDBCollectionStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.mongodb.tools.mongodb_collection_stats_tool import (
    _map_get_mongodb_collection_stats,
    get_mongodb_collection_stats,
)
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
        "integrations.mongodb.tools.mongodb_collection_stats_tool.get_collection_stats",
        return_value=fake_result,
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
        "integrations.mongodb.tools.mongodb_collection_stats_tool.get_collection_stats",
        return_value=fake_result,
    ):
        result = get_mongodb_collection_stats(
            connection_string="mongodb://invalid",
            database="my-db",
            collection="my-collection",
        )
    assert "error" in result


class TestMapGetMongodbCollectionStats:
    def test_records_entry_with_size_and_index_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_collection_stats(
            evidence,
            {
                "available": True,
                "ns": "my-db.my-collection",
                "count": 1000,
                "size_bytes": 2 * 1024 * 1024,
                "index_count": 3,
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_mongodb_collection_stats"
        assert (
            entries[0]["summary"] == "'my-db.my-collection': 1000 document(s), 2.0MB, 3 index(es)"
        )

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_mongodb_collection_stats(
            evidence, {"available": False, "error": "Connection refused"}, {}
        )

        assert "catalog_entries" not in evidence
