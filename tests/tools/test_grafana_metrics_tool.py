"""Tests for GrafanaMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GrafanaMetricsTool import query_grafana_metrics
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_metrics.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_metrics.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_metrics.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert "metric_name" in params
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_run_with_backend() -> None:
    mock_backend = MagicMock()
    mock_backend.query_timeseries.return_value = {
        "data": {"result": [{"metric": {}, "values": [[1000, "42"]]}]}
    }
    result = query_grafana_metrics(metric_name="pipeline_runs_total", grafana_backend=mock_backend)
    assert result["available"] is True
    assert result["total_series"] == 1


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.GrafanaMetricsTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_metrics(metric_name="cpu_usage", grafana_endpoint="http://grafana")
    assert result["available"] is False


def test_run_no_mimir_datasource() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.mimir_datasource_uid = None
    with patch("app.tools.GrafanaMetricsTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_metrics(metric_name="cpu_usage", grafana_endpoint="http://grafana")
    assert result["available"] is False
    assert "Mimir" in result["error"]


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.mimir_datasource_uid = "mimir-uid"
    mock_client.account_id = "acc-1"
    mock_client.query_mimir.return_value = {
        "success": True,
        "metrics": [{"name": "pipeline_runs_total"}],
        "total_series": 1,
    }
    with patch("app.tools.GrafanaMetricsTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_metrics(metric_name="pipeline_runs_total", grafana_endpoint="http://grafana")
    assert result["available"] is True
    assert result["total_series"] == 1
