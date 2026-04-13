"""Tests for MongoDBAtlasAlertsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.MongoDBAtlasAlertsTool import get_mongodb_atlas_alerts
from tests.tools.conftest import BaseToolContract


class TestMongoDBAtlasAlertsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_mongodb_atlas_alerts.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_mongodb_atlas_alerts.__opensre_registered_tool__
    assert rt.name == "get_mongodb_atlas_alerts"
    assert rt.source == "mongodb_atlas"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "mongodb_atlas",
        "available": True,
        "total_alerts": 1,
        "alerts": [{"id": "alert-1", "event_type": "OUTSIDE_METRIC_THRESHOLD", "status": "OPEN"}],
    }
    with patch("app.tools.MongoDBAtlasAlertsTool.get_alerts", return_value=fake_result):
        result = get_mongodb_atlas_alerts(api_public_key="pub", api_private_key="priv", project_id="proj")
    assert result["available"] is True
    assert result["total_alerts"] == 1


def test_run_error_propagated() -> None:
    with patch("app.tools.MongoDBAtlasAlertsTool.get_alerts", return_value={"source": "mongodb_atlas", "available": False, "error": "auth failed"}):
        result = get_mongodb_atlas_alerts(api_public_key="bad", api_private_key="bad", project_id="proj")
    assert "error" in result
