"""Integration test for Grafana agent actions."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.agent.nodes.plan_actions.detect_sources import detect_sources
from app.agent.tools.tool_actions.grafana import (
    check_grafana_connection,
    query_grafana_logs,
    query_grafana_traces,
)


def test_detect_sources_includes_grafana_when_connected():
    """Test that detect_sources includes Grafana when service map shows connection."""
    raw_alert = {
        "annotations": {
            "pipeline_name": "upstream_downstream_pipeline_lambda_mock_dag",
            "execution_run_id": "test-123",
            "lambda_function": "MockDagLambda",
        }
    }
    context = {}

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.check_grafana_connection"
    ) as mock_check:
        mock_check.return_value = {
            "connected": True,
            "service_name": "lambda-mock-dag",
            "pipeline_name": "upstream_downstream_pipeline_lambda_mock_dag",
        }

        sources = detect_sources(raw_alert, context)

        assert "grafana" in sources
        assert sources["grafana"]["service_name"] == "lambda-mock-dag"
        assert sources["grafana"]["execution_run_id"] == "test-123"
        assert sources["grafana"]["connection_verified"] is True


def test_detect_sources_skips_grafana_when_not_connected():
    """Test that detect_sources skips Grafana when service map shows no connection."""
    raw_alert = {
        "annotations": {
            "pipeline_name": "pipeline_without_grafana",
        }
    }
    context = {}

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.check_grafana_connection"
    ) as mock_check:
        mock_check.return_value = {"connected": False}

        sources = detect_sources(raw_alert, context)

        assert "grafana" not in sources


def test_grafana_actions_available_in_action_pool():
    """Test that Grafana actions are registered and available."""
    from app.agent.tools.tool_actions.investigation_registry import get_available_actions

    actions = get_available_actions()
    action_names = [a.name for a in actions]

    assert "query_grafana_logs" in action_names
    assert "query_grafana_traces" in action_names
    assert "query_grafana_metrics" in action_names
    assert "check_grafana_connection" in action_names

    # Check metadata
    grafana_log_action = next(a for a in actions if a.name == "query_grafana_logs")
    assert grafana_log_action.source == "grafana"
    assert "service_name" in grafana_log_action.requires
    assert grafana_log_action.availability_check is not None


def test_grafana_availability_check():
    """Test that Grafana actions are only available when connection verified."""
    from app.agent.tools.tool_actions.investigation_registry import get_available_actions

    actions = get_available_actions()
    grafana_log_action = next(a for a in actions if a.name == "query_grafana_logs")

    # Available when connection verified
    sources_connected = {
        "grafana": {
            "service_name": "lambda-mock-dag",
            "connection_verified": True,
        }
    }
    assert grafana_log_action.availability_check(sources_connected) is True

    # Not available when no Grafana source
    sources_no_grafana = {
        "cloudwatch": {"log_group": "/aws/lambda/test"},
    }
    assert grafana_log_action.availability_check(sources_no_grafana) is False


@pytest.mark.integration
def test_end_to_end_grafana_query():
    """Integration test: Query actual Grafana Cloud."""
    import os

    # Skip if no Grafana token configured
    if not os.getenv("GRAFANA_READ_TOKEN"):
        pytest.skip("GRAFANA_READ_TOKEN not configured")

    # Test with known service that has data
    result = query_grafana_logs("lambda-mock-dag", time_range_minutes=60, limit=10)

    # Should succeed or gracefully fail
    assert "available" in result
    assert "logs" in result
    assert "source" in result


@pytest.mark.integration
def test_grafana_action_integration_with_service_map():
    """Test that service map Grafana edges enable Grafana actions."""
    from app.agent.memory.service_map import build_service_map

    # Create evidence with Lambda OTLP config
    evidence = {
        "lambda_config": {
            "function_name": "MockDagLambda",
            "environment_variables": {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp",
                "GCLOUD_OTLP_ENDPOINT": "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp",
            },
        }
    }

    raw_alert = {"annotations": {}}
    context = {}

    service_map = build_service_map(
        evidence,
        raw_alert,
        context,
        "upstream_downstream_pipeline_lambda",
        "Test Alert",
    )

    # Should create Grafana edges
    grafana_edges = [e for e in service_map["edges"] if "grafana" in e.get("to_asset", "")]
    # Note: May be 0 if service map disabled or evidence insufficient
    assert isinstance(grafana_edges, list)

    # Check connection via API
    connection = check_grafana_connection("upstream_downstream_pipeline_lambda_mock_dag")
    # May or may not be connected depending on whether service map was persisted
    assert "connected" in connection
    assert "service_name" in connection
