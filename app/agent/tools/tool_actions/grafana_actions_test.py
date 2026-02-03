"""Unit tests for Grafana Cloud investigation actions."""

from unittest.mock import MagicMock, patch

from app.agent.tools.tool_actions.grafana_actions import (
    check_grafana_connection,
    query_grafana_logs,
    query_grafana_metrics,
    query_grafana_traces,
)


def test_query_grafana_logs_success():
    """Test query_grafana_logs returns logs when available."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.query_loki.return_value = {
            "success": True,
            "logs": [
                {"timestamp": "123", "message": "test log", "labels": {}},
                {"timestamp": "124", "message": "error log", "labels": {}},
            ],
            "total_logs": 2,
        }
        mock_client.return_value = mock_instance

        result = query_grafana_logs("lambda-mock-dag", execution_run_id="test-123")

        assert result["available"] is True
        assert result["source"] == "grafana_loki"
        assert len(result["logs"]) == 2
        assert len(result["error_logs"]) == 1  # One error log


def test_query_grafana_logs_failure():
    """Test query_grafana_logs handles failures gracefully."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.query_loki.return_value = {
            "success": False,
            "error": "Auth failed",
            "logs": [],
        }
        mock_client.return_value = mock_instance

        result = query_grafana_logs("lambda-mock-dag")

        assert result["available"] is False
        assert "error" in result
        assert result["logs"] == []


def test_query_grafana_traces_success():
    """Test query_grafana_traces returns traces with spans."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = MagicMock()
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
        mock_client.return_value = mock_instance

        result = query_grafana_traces("prefect-etl-pipeline", execution_run_id="test-123")

        assert result["available"] is True
        assert result["source"] == "grafana_tempo"
        assert len(result["traces"]) == 1
        assert len(result["pipeline_spans"]) == 2
        assert result["pipeline_spans"][0]["span_name"] == "validate_data"


def test_query_grafana_metrics_success():
    """Test query_grafana_metrics returns metric series."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.query_mimir.return_value = {
            "success": True,
            "metrics": [
                {"metric": {"service_name": "lambda-mock-dag"}, "value": [1234, "42"]},
            ],
            "total_series": 1,
        }
        mock_client.return_value = mock_instance

        result = query_grafana_metrics("pipeline_runs_total", service_name="lambda-mock-dag")

        assert result["available"] is True
        assert result["source"] == "grafana_mimir"
        assert len(result["metrics"]) == 1


def test_check_grafana_connection_connected():
    """Test check_grafana_connection detects connected pipelines."""
    with patch("app.agent.memory.service_map.load_service_map") as mock_load:
        mock_load.return_value = {
            "enabled": True,
            "assets": [],
            "edges": [
                {
                    "from_asset": "pipeline:upstream_downstream_pipeline_lambda",
                    "to_asset": "grafana_datasource:tracerbio",
                    "type": "exports_telemetry_to",
                }
            ],
        }

        result = check_grafana_connection("upstream_downstream_pipeline_lambda")

        assert result["connected"] is True
        assert result["service_name"] == "upstream_downstream_pipeline_lambda"  # No mapping for generic name


def test_check_grafana_connection_not_connected():
    """Test check_grafana_connection handles pipelines without Grafana."""
    with patch("app.agent.memory.service_map.load_service_map") as mock_load:
        mock_load.return_value = {
            "enabled": True,
            "assets": [],
            "edges": [],
        }

        result = check_grafana_connection("unknown_pipeline")

        assert result["connected"] is False
        assert "No Grafana edge" in result["reason"]
