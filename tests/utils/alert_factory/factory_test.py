"""
Test alert factory with remote LangSmith platform.

Verifies alert creation and submission to deployed LangGraph API.
"""

import os
from datetime import UTC, datetime

import pytest
import requests

from config.grafana_config import load_env
from tests.utils.alert_factory.factory import create_alert, from_pipeline_run
from tests.utils.alert_factory.formatters.grafana import format_as_grafana
from tests.utils.alert_factory.intent import AlertIntent

load_env()

def test_alert_intent_creation():
    """Test that AlertIntent captures core information correctly."""
    timestamp = datetime.now(UTC).isoformat()
    intent = AlertIntent(
        pipeline_name="test_pipeline",
        run_name="test_run_001",
        status="failed",
        timestamp=timestamp,
        severity="warning",
        alert_name="CustomAlert",
        environment="staging",
        annotations={"foo": "bar"},
    )

    assert intent.pipeline_name == "test_pipeline"
    assert intent.severity == "warning"
    assert intent.alert_name == "CustomAlert"
    assert intent.environment == "staging"
    assert intent.annotations["foo"] == "bar"


def test_grafana_formatter():
    """Test that Grafana formatter renders intent correctly."""
    timestamp = datetime.now(UTC).isoformat()
    intent = AlertIntent(
        pipeline_name="test_pipeline",
        run_name="test_run_001",
        status="failed",
        timestamp=timestamp,
    )

    payload = format_as_grafana(intent)

    assert payload["alerts"][0]["labels"]["pipeline_name"] == "test_pipeline"
    assert payload["alerts"][0]["labels"]["alertname"] == "PipelineFailure"
    assert payload["alerts"][0]["labels"]["severity"] == "critical"
    assert payload["alerts"][0]["labels"]["environment"] == "production"


def test_factory_from_pipeline_run():
    """Test that from_pipeline_run produces valid payloads."""
    timestamp = datetime.now(UTC).isoformat()
    payload = from_pipeline_run(
        pipeline_name="test_pipeline",
        run_name="test_run_001",
        status="failed",
        timestamp=timestamp,
        severity="high",
        alert_name="FailureEvent",
    )

    assert payload["alerts"][0]["labels"]["severity"] == "high"
    assert payload["alerts"][0]["labels"]["alertname"] == "FailureEvent"

def test_create_alert_backwards_compatibility():
    """Test that create_alert still works as expected."""
    timestamp = datetime.now(UTC).isoformat()
    alert = create_alert(
        pipeline_name="test_pipeline",
        run_name="test_run_001",
        status="failed",
        timestamp=timestamp,
        annotations={"test_key": "test_value"},
    )

    assert alert is not None
    assert "alerts" in alert
    assert alert["version"] == "4"
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
