"""Unit tests for Grafana Cloud investigation actions."""

from unittest.mock import MagicMock, patch

from app.agent.tools.tool_actions.grafana_actions import (
    check_grafana_connection,
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
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = _create_mock_client()
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
        assert result["account_id"] == "tracerbio"


def test_query_grafana_logs_not_configured():
    """Test query_grafana_logs handles unconfigured accounts."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = _create_mock_client(is_configured=False, account_id="customer1")
        mock_client.return_value = mock_instance

        result = query_grafana_logs("lambda-mock-dag", account_id="customer1")

        assert result["available"] is False
        assert "not configured" in result["error"]


def test_query_grafana_logs_failure():
    """Test query_grafana_logs handles failures gracefully."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = _create_mock_client()
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
        mock_client.return_value = mock_instance

        result = query_grafana_traces("prefect-etl-pipeline", execution_run_id="test-123")

        assert result["available"] is True
        assert result["source"] == "grafana_tempo"
        assert len(result["traces"]) == 1
        assert len(result["pipeline_spans"]) == 2
        assert result["pipeline_spans"][0]["span_name"] == "validate_data"
        assert result["account_id"] == "tracerbio"


def test_query_grafana_metrics_success():
    """Test query_grafana_metrics returns metric series."""
    with patch("app.agent.tools.tool_actions.grafana_actions.get_grafana_client") as mock_client:
        mock_instance = _create_mock_client()
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
        assert result["account_id"] == "tracerbio"


def test_check_grafana_connection_connected():
    """Test check_grafana_connection detects connected pipelines."""
    with (
        patch("app.agent.memory.service_map.load_service_map") as mock_load,
        patch(
            "app.agent.tools.clients.grafana_config.get_grafana_config"
        ) as mock_config,
    ):
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
        mock_config.return_value = MagicMock(is_configured=True, account_id="tracerbio")

        result = check_grafana_connection("upstream_downstream_pipeline_lambda")

        assert result["connected"] is True
        assert result["account_id"] == "tracerbio"
        assert result["service_name"] == "upstream_downstream_pipeline_lambda"


def test_check_grafana_connection_not_connected():
    """Test check_grafana_connection handles pipelines without Grafana."""
    with (
        patch("app.agent.memory.service_map.load_service_map") as mock_load,
        patch(
            "app.agent.tools.clients.grafana_config.get_grafana_config"
        ) as mock_config,
    ):
        mock_load.return_value = {
            "enabled": True,
            "assets": [],
            "edges": [],
        }
        mock_config.return_value = MagicMock(is_configured=True, account_id="tracerbio")

        result = check_grafana_connection("unknown_pipeline")

        assert result["connected"] is False
        assert "No Grafana edge" in result["reason"]


def test_check_grafana_connection_account_not_configured():
    """Test check_grafana_connection fails when account token is missing."""
    with (
        patch("app.agent.memory.service_map.load_service_map") as mock_load,
        patch(
            "app.agent.tools.clients.grafana_config.get_grafana_config"
        ) as mock_config,
    ):
        mock_load.return_value = {
            "enabled": True,
            "assets": [],
            "edges": [
                {
                    "from_asset": "pipeline:test_pipeline",
                    "to_asset": "grafana_datasource:customer1",
                    "type": "exports_telemetry_to",
                }
            ],
        }
        mock_config.return_value = MagicMock(is_configured=False, account_id="customer1")

        result = check_grafana_connection("test_pipeline")

        assert result["connected"] is False
        assert "not configured" in result["reason"]
