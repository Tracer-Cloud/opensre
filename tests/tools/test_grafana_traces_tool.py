"""Tests for GrafanaTracesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GrafanaTracesTool import query_grafana_traces
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaTracesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_traces.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_traces.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {"_backend": MagicMock()}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_traces.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["service_name"] == "my-service"
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.GrafanaTracesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_traces(service_name="svc", grafana_endpoint="http://grafana")
    assert result["available"] is False


def test_run_no_tempo_datasource() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.tempo_datasource_uid = None
    with patch("app.tools.GrafanaTracesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_traces(service_name="svc", grafana_endpoint="http://grafana")
    assert result["available"] is False
    assert "Tempo" in result["error"]


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.tempo_datasource_uid = "tempo-uid"
    mock_client.account_id = "acc-1"
    mock_client.query_tempo.return_value = {
        "success": True,
        "traces": [
            {"traceId": "t1", "spans": [{"name": "extract_data", "attributes": {}}]},
        ],
        "total_traces": 1,
    }
    with patch("app.tools.GrafanaTracesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_traces(service_name="svc", grafana_endpoint="http://grafana")
    assert result["available"] is True
    assert result["total_traces"] == 1
    assert len(result["pipeline_spans"]) == 1


def test_run_with_injected_backend() -> None:
    backend = MagicMock()
    backend.query_traces.return_value = {
        "traces": [
            {"traceId": "t1", "spans": [{"name": "extract_data", "attributes": {}}]},
        ],
    }
    result = query_grafana_traces(service_name="svc", grafana_backend=backend)
    assert result["available"] is True
    assert result["source"] == "grafana_tempo"
    assert result["total_traces"] == 1
    assert len(result["pipeline_spans"]) == 1
    backend.query_traces.assert_called_once_with(service_name="svc")


def test_run_with_injected_backend_empty_traces() -> None:
    backend = MagicMock()
    backend.query_traces.return_value = {"traces": [], "metrics": {}}
    result = query_grafana_traces(service_name="svc", grafana_backend=backend)
    assert result["available"] is True
    assert result["traces"] == []
    assert result["total_traces"] == 0


def test_run_filters_by_execution_run_id() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.tempo_datasource_uid = "tempo-uid"
    mock_client.account_id = "acc-1"
    mock_client.query_tempo.return_value = {
        "success": True,
        "traces": [
            {
                "traceId": "t1",
                "spans": [{"name": "load_data", "attributes": {"execution.run_id": "run-42"}}],
            },
            {
                "traceId": "t2",
                "spans": [{"name": "load_data", "attributes": {"execution.run_id": "run-99"}}],
            },
        ],
        "total_traces": 2,
    }
    with patch("app.tools.GrafanaTracesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_traces(
            service_name="svc",
            execution_run_id="run-42",
            grafana_endpoint="http://grafana",
        )
    assert result["available"] is True
    assert all(
        any(s.get("attributes", {}).get("execution.run_id") == "run-42" for s in t["spans"])
        for t in result["traces"]
    )
