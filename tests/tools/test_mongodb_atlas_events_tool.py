"""Tests for MongoDBAtlasEventsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBAtlasEventsTool import get_mongodb_atlas_cluster_events
from tests.tools.conftest import BaseToolContract


class TestMongoDBAtlasEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_atlas_cluster_events.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_atlas_cluster_events.__opensre_registered_tool__
    assert rt.name == "get_mongodb_atlas_cluster_events"
    assert rt.source == "mongodb_atlas"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mongodb_atlas",
        "available": True,
        "total_events": 2,
        "events": [
            {"id": "evt-1", "event_type": "CLUSTER_READY", "cluster_name": "prod"},
            {
                "id": "evt-2",
                "event_type": "REPLICATION_OPLOG_WINDOW_RUNNING_OUT",
                "cluster_name": "prod",
            },
        ],
    }
    with patch("app.tools.MongoDBAtlasEventsTool.get_cluster_events", return_value=fake_result):
        result = get_mongodb_atlas_cluster_events(
            api_public_key="pub",
            api_private_key="priv",
            project_id="proj",
            cluster_name="prod",
        )
    assert result["available"] is True
    assert result["total_events"] == 2


def test_run_error_propagated() -> None:
    with patch(
        "app.tools.MongoDBAtlasEventsTool.get_cluster_events",
        return_value={"source": "mongodb_atlas", "available": False, "error": "auth failed"},
    ):
        result = get_mongodb_atlas_cluster_events(
            api_public_key="bad",
            api_private_key="bad",
            project_id="proj",
            cluster_name="prod",
        )
    assert "error" in result
