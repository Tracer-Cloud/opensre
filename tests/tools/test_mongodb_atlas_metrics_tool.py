"""Tests for MongoDBAtlasMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBAtlasMetricsTool import get_mongodb_atlas_cluster_metrics
from tests.tools.conftest import BaseToolContract


class TestMongoDBAtlasMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_atlas_cluster_metrics.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_atlas_cluster_metrics.__opensre_registered_tool__
    assert rt.name == "get_mongodb_atlas_cluster_metrics"
    assert rt.source == "mongodb_atlas"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mongodb_atlas",
        "available": True,
        "process_id": "host:27017",
        "measurements": {"CONNECTIONS": {"value": 42, "units": "SCALAR"}},
    }
    with patch("app.tools.MongoDBAtlasMetricsTool.get_cluster_metrics", return_value=fake_result):
        result = get_mongodb_atlas_cluster_metrics(
            api_public_key="pub", api_private_key="priv",
            project_id="proj", cluster_name="prod",
        )
    assert result["available"] is True
    assert "CONNECTIONS" in result["measurements"]


def test_run_error_propagated() -> None:
    with patch("app.tools.MongoDBAtlasMetricsTool.get_cluster_metrics", return_value={"source": "mongodb_atlas", "available": False, "error": "auth failed"}):
        result = get_mongodb_atlas_cluster_metrics(
            api_public_key="bad", api_private_key="bad",
            project_id="proj", cluster_name="prod",
        )
    assert "error" in result
