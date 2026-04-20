"""Tests for MongoDBAtlasPerformanceAdvisorTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBAtlasPerformanceAdvisorTool import get_mongodb_atlas_performance_advisor
from tests.tools.conftest import BaseToolContract


class TestMongoDBAtlasPerformanceAdvisorToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_atlas_performance_advisor.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_atlas_performance_advisor.__opensre_registered_tool__
    assert rt.name == "get_mongodb_atlas_performance_advisor"
    assert rt.source == "mongodb_atlas"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mongodb_atlas",
        "available": True,
        "process_id": "host:27017",
        "total_suggested_indexes": 1,
        "suggested_indexes": [{"namespace": "db.coll", "index": [{"key": 1}]}],
        "total_slow_queries": 0,
        "slow_queries": [],
    }
    with patch(
        "app.tools.MongoDBAtlasPerformanceAdvisorTool.get_performance_advisor",
        return_value=fake_result,
    ):
        result = get_mongodb_atlas_performance_advisor(
            api_public_key="pub",
            api_private_key="priv",
            project_id="proj",
            cluster_name="prod",
        )
    assert result["available"] is True
    assert result["total_suggested_indexes"] == 1


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBAtlasPerformanceAdvisorTool.get_performance_advisor",
        return_value={"source": "mongodb_atlas", "available": False, "error": "auth failed"},
    ):
        result = get_mongodb_atlas_performance_advisor(
            api_public_key="bad",
            api_private_key="bad",
            project_id="proj",
            cluster_name="prod",
        )
    assert "error" in result
