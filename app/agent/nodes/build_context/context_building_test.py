import os
from typing import cast
from unittest.mock import patch

import pytest

from app.agent.nodes.build_context.context_building import build_investigation_context
from app.agent.nodes.build_context.sources.datadog_context import build_context_datadog
from app.agent.nodes.build_context.sources.grafana_context import build_context_grafana
from app.agent.state import AgentState


@pytest.mark.skipif(not os.getenv("JWT_TOKEN"), reason="JWT_TOKEN not set")
def test_build_investigation_context_tracer_web_integration() -> None:
    jwt_token = os.getenv("JWT_TOKEN")
    assert jwt_token, "JWT_TOKEN must be set for this integration test"

    context = build_investigation_context({"plan_sources": ["tracer_web"]})

    tracer_web_run = context.get("tracer_web_run")
    assert tracer_web_run is not None
    assert isinstance(tracer_web_run.get("found"), bool)


# ---------------------------------------------------------------------------
# Grafana context source - unit tests
# ---------------------------------------------------------------------------


def test_grafana_context_no_credentials_returns_empty() -> None:
    state = cast(AgentState, {"resolved_integrations": {}})
    result = build_context_grafana(state)

    assert result.data["service_names"] == []
    assert result.data["connection_verified"] is False
    assert result.error is None


def test_grafana_context_returns_service_names() -> None:
    state = cast(AgentState, {
        "resolved_integrations": {
            "grafana": {"endpoint": "https://test.grafana.net", "api_key": "glsa_test"}
        }
    })
    mock_result = {"available": True, "service_names": ["etl-pipeline", "upstream-pipeline"]}

    with patch(
        "app.agent.nodes.build_context.sources.grafana_context.query_grafana_service_names",
        return_value=mock_result,
    ):
        result = build_context_grafana(state)

    assert result.data["connection_verified"] is True
    assert result.data["service_names"] == ["etl-pipeline", "upstream-pipeline"]
    assert result.data["grafana_endpoint"] == "https://test.grafana.net"
    assert result.error is None


def test_grafana_context_graceful_on_api_error() -> None:
    state = cast(AgentState, {
        "resolved_integrations": {
            "grafana": {"endpoint": "https://test.grafana.net", "api_key": "glsa_test"}
        }
    })

    with patch(
        "app.agent.nodes.build_context.sources.grafana_context.query_grafana_service_names",
        side_effect=RuntimeError("connection refused"),
    ):
        result = build_context_grafana(state)

    assert result.data["service_names"] == []
    assert result.data["connection_verified"] is False
    assert "error" in result.data


# ---------------------------------------------------------------------------
# Datadog context source - unit tests
# ---------------------------------------------------------------------------


def test_datadog_context_no_credentials_returns_empty() -> None:
    state = cast(AgentState, {"resolved_integrations": {}})
    result = build_context_datadog(state)

    assert result.data["monitors"] == []
    assert result.data["connection_verified"] is False
    assert result.error is None


def test_datadog_context_returns_monitors() -> None:
    state = cast(AgentState, {
        "pipeline_name": "etl-pipeline",
        "resolved_integrations": {
            "datadog": {"api_key": "dd_api", "app_key": "dd_app", "site": "datadoghq.com"}
        },
    })
    mock_result = {
        "available": True,
        "monitors": [{"id": 1, "name": "ETL Pipeline Failure", "overall_state": "Alert"}],
        "total": 1,
    }

    with patch(
        "app.agent.nodes.build_context.sources.datadog_context.query_datadog_monitors",
        return_value=mock_result,
    ):
        result = build_context_datadog(state)

    assert result.data["connection_verified"] is True
    assert len(result.data["monitors"]) == 1
    assert result.data["monitors"][0]["name"] == "ETL Pipeline Failure"
    assert result.data["total"] == 1
    assert result.error is None


def test_datadog_context_graceful_on_api_error() -> None:
    state = cast(AgentState, {
        "resolved_integrations": {
            "datadog": {"api_key": "dd_api", "app_key": "dd_app", "site": "datadoghq.com"}
        }
    })

    with patch(
        "app.agent.nodes.build_context.sources.datadog_context.query_datadog_monitors",
        side_effect=RuntimeError("timeout"),
    ):
        result = build_context_datadog(state)

    assert result.data["monitors"] == []
    assert result.data["connection_verified"] is False
    assert "error" in result.data


# ---------------------------------------------------------------------------
# Integration tests - skip if credentials not set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("GRAFANA_READ_TOKEN"),
    reason="GRAFANA_READ_TOKEN not set",
)
def test_grafana_context_integration() -> None:
    from config.grafana_config import get_grafana_instance_url, get_grafana_read_token

    endpoint = get_grafana_instance_url()
    api_key = get_grafana_read_token()

    state = cast(AgentState, {
        "resolved_integrations": {
            "grafana": {"endpoint": endpoint, "api_key": api_key}
        }
    })
    result = build_context_grafana(state)

    assert isinstance(result.data["service_names"], list)
    assert isinstance(result.data["connection_verified"], bool)


@pytest.mark.skipif(
    not (os.getenv("DD_API_KEY") and os.getenv("DD_APP_KEY")),
    reason="DD_API_KEY and DD_APP_KEY not set",
)
def test_datadog_context_integration() -> None:
    state = cast(AgentState, {
        "resolved_integrations": {
            "datadog": {
                "api_key": os.environ["DD_API_KEY"],
                "app_key": os.environ["DD_APP_KEY"],
                "site": os.getenv("DD_SITE", "datadoghq.com"),
            }
        }
    })
    result = build_context_datadog(state)

    assert isinstance(result.data["monitors"], list)
    assert isinstance(result.data["connection_verified"], bool)
