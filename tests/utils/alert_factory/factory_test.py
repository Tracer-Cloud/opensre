"""
Test alert factory with remote LangSmith platform.

Verifies alert creation and submission to deployed LangGraph API.
"""

import os
from datetime import UTC, datetime

import pytest
import requests
from dotenv import load_dotenv

from tests.conftest import get_test_config
from tests.utils.alert_factory.factory import create_alert

load_dotenv()


def test_alert_factory_creates_valid_alert():
    """Test that alert factory produces valid Grafana-style alerts."""
    timestamp = datetime.now(UTC).isoformat()

    alert = create_alert(
        pipeline_name="test_pipeline",
        run_name="test_run_001",
        status="failed",
        timestamp=timestamp,
        annotations={
            "test_key": "test_value",
            "error": "Test error message",
        },
    )

    assert alert is not None
    assert "alerts" in alert
    assert alert["version"] == "4"
    assert len(alert["alerts"]) == 1
    assert alert["alerts"][0]["labels"]["alertname"] == "PipelineFailure"
    assert "test_key" in alert["commonAnnotations"]


def test_fire_alert_to_remote_platform():
    """Test firing alert to remote LangSmith platform."""
    endpoint = os.getenv("LANGGRAPH_ENDPOINT")

    if not endpoint or "localhost" in endpoint:
        pytest.skip("Remote LANGGRAPH_ENDPOINT not configured")

    timestamp = datetime.now(UTC).isoformat()

    alert = create_alert(
        pipeline_name="alert_factory_test",
        run_name=f"test_run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        status="failed",
        timestamp=timestamp,
        annotations={
            "test_source": "alert_factory_remote_test",
            "error": "Test alert from alert factory",
        },
    )

    payload = {
        "input": {
            "alert_name": "Alert factory test",
            "pipeline_name": "alert_factory_test",
            "severity": "critical",
            "raw_alert": alert,
        },
        "config": {
            "metadata": {
                "test": "alert_factory_remote",
            }
        },
        "stream_mode": ["values"],
    }

    response = requests.post(endpoint, json=payload, timeout=30)

    assert response.status_code == 200, f"Failed to fire alert: {response.text}"
    print(f"✓ Alert fired to remote platform: {endpoint}")
    print(f"  Status: {response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
