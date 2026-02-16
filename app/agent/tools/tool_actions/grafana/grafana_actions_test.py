"""Unit tests for Grafana Cloud investigation actions."""

from unittest.mock import MagicMock, patch

from app.agent.tools.tool_actions.grafana.grafana_actions import (
    query_grafana_logs,
    query_grafana_metrics,
    query_grafana_traces,
)


def _create_mock_client(is_configured: bool = True, account_id: str = "tracerbio") -> MagicMock:
    """Create a mock Grafana client with required properties."""
    mock = MagicMock()
    mock.is_configured = is_configured
    mock.account_id = account_id
    return mock


def test_query_grafana_logs_success():
    """Test query_grafana_logs returns logs when available."""
    mock_instance = _create_mock_client()
    mock_instance.query_loki.return_value = {
        "success": True,
        "logs": [
            {"timestamp": "123", "message": "test log", "labels": {}},
            {"timestamp": "124", "message": "error log", "labels": {}},
        ],
        "total_logs": 2,
    }

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.get_grafana_client_from_credentials",
        return_value=mock_instance,
    ):
        result = query_grafana_logs(
            "lambda-mock-dag",
            execution_run_id="test-123",
            grafana_endpoint="https://test.grafana.net",
            grafana_api_key="glsa_test",
        )

    assert result["available"] is True
    assert result["source"] == "grafana_loki"
    assert len(result["logs"]) == 2
    assert len(result["error_logs"]) == 1  # One error log
    assert result["account_id"] == "tracerbio"


def test_query_grafana_logs_not_configured():
    """Test query_grafana_logs handles missing credentials."""
    result = query_grafana_logs("lambda-mock-dag")

    assert result["available"] is False
    assert "not configured" in result["error"]


def test_query_grafana_logs_failure():
    """Test query_grafana_logs handles query failures gracefully."""
    mock_instance = _create_mock_client()
    mock_instance.query_loki.return_value = {
        "success": False,
        "error": "Auth failed",
        "logs": [],
    }

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.get_grafana_client_from_credentials",
        return_value=mock_instance,
    ):
        result = query_grafana_logs(
            "lambda-mock-dag",
            grafana_endpoint="https://test.grafana.net",
            grafana_api_key="glsa_test",
        )

    assert result["available"] is False
    assert "error" in result
    assert result["logs"] == []


def test_query_grafana_traces_success():
    """Test query_grafana_traces returns traces with spans."""
    mock_instance = _create_mock_client()
    mock_instance.query_tempo.return_value = {
        "success": True,
        "traces": [
            {
                "trace_id": "abc123",
                "spans": [
                    {
                        "name": "validate_data",
                        "attributes": {"execution.run_id": "test-123", "record_count": 10},
                    },
                    {
                        "name": "transform_data",
                        "attributes": {"execution.run_id": "test-123"},
                    },
                ],
            }
        ],
        "total_traces": 1,
    }

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.get_grafana_client_from_credentials",
        return_value=mock_instance,
    ):
        result = query_grafana_traces(
            "prefect-etl-pipeline",
            execution_run_id="test-123",
            grafana_endpoint="https://test.grafana.net",
            grafana_api_key="glsa_test",
        )

    assert result["available"] is True
    assert result["source"] == "grafana_tempo"
    assert len(result["traces"]) == 1
    assert len(result["pipeline_spans"]) == 2
    assert result["pipeline_spans"][0]["span_name"] == "validate_data"
    assert result["account_id"] == "tracerbio"


def test_query_grafana_metrics_success():
    """Test query_grafana_metrics returns metric series."""
    mock_instance = _create_mock_client()
    mock_instance.query_mimir.return_value = {
        "success": True,
        "metrics": [
            {"metric": {"service_name": "lambda-mock-dag"}, "value": [1234, "42"]},
        ],
        "total_series": 1,
    }

    with patch(
        "app.agent.tools.tool_actions.grafana.grafana_actions.get_grafana_client_from_credentials",
        return_value=mock_instance,
    ):
        result = query_grafana_metrics(
            "pipeline_runs_total",
            service_name="lambda-mock-dag",
            grafana_endpoint="https://test.grafana.net",
            grafana_api_key="glsa_test",
        )

    assert result["available"] is True
    assert result["source"] == "grafana_mimir"
    assert len(result["metrics"]) == 1
    assert result["account_id"] == "tracerbio"
