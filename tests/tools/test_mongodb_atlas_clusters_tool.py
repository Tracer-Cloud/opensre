"""Tests for MongoDBAtlasClustersTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBAtlasClustersTool import get_mongodb_atlas_clusters
from tests.tools.conftest import BaseToolContract


class TestMongoDBAtlasClustersToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_atlas_clusters.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_atlas_clusters.__opensre_registered_tool__
    assert rt.name == "get_mongodb_atlas_clusters"
    assert rt.source == "mongodb_atlas"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mongodb_atlas",
        "available": True,
        "total_clusters": 1,
        "clusters": [{"name": "prod-cluster", "state": "IDLE", "mongo_db_version": "7.0.12"}],
    }
    with patch("app.tools.MongoDBAtlasClustersTool.get_clusters", return_value=fake_result):
        result = get_mongodb_atlas_clusters(api_public_key="pub", api_private_key="priv", project_id="proj")
    assert result["available"] is True
    assert result["total_clusters"] == 1


def test_run_error_propagated() -> None:
    with patch("app.tools.MongoDBAtlasClustersTool.get_clusters", return_value={"source": "mongodb_atlas", "available": False, "error": "auth failed"}):
        result = get_mongodb_atlas_clusters(api_public_key="bad", api_private_key="bad", project_id="proj")
    assert "error" in result
