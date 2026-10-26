"""Tests for GrafanaLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.grafana.tools import query_grafana_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_logs.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_logs.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {"_backend": MagicMock()}}) is True
    assert rt.is_available({"grafana": {"endpoint": "https://grafana.example.com"}}) is True
    assert rt.is_available({"grafana_local": {"endpoint": "http://localhost:3000"}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_is_available_accepts_classified_grafana_model() -> None:
    from integrations.config_models import GrafanaIntegrationConfig

    rt = query_grafana_logs.__opensre_registered_tool__
    assert (
        rt.is_available(
            {
                "grafana": GrafanaIntegrationConfig(
                    endpoint="https://tracerbio.grafana.net",
                    api_key="glsa_test",
                )
            }
        )
        is True
    )


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["service_name"] == "my-service"
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_basic_auth_is_runtime_only() -> None:
    rt = query_grafana_logs.__opensre_registered_tool__
    basic_auth_params = {"grafana_username", "grafana_password"}
    properties = rt.input_schema.get("properties", {})

    assert basic_auth_params.isdisjoint(properties)
    assert basic_auth_params <= set(rt.injected_params)


def test_extract_params_accepts_catalog_grafana_shape() -> None:
    rt = query_grafana_logs.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "grafana": {
                "endpoint": "https://grafana.example.com",
                "api_key": "glsa_test",
                "service_name": "api",
            }
        }
    )
    assert params["service_name"] == "api"
    assert params["grafana_endpoint"] == "https://grafana.example.com"
    assert params["grafana_api_key"] == "glsa_test"


def test_extract_params_accepts_classified_grafana_model() -> None:
    from integrations.config_models import GrafanaIntegrationConfig

    rt = query_grafana_logs.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "grafana": GrafanaIntegrationConfig(
                endpoint="https://tracerbio.grafana.net",
                api_key="glsa_test",
            )
        }
    )
    assert params["grafana_endpoint"] == "https://tracerbio.grafana.net"
    assert params["grafana_api_key"] == "glsa_test"


def test_run_with_backend_returns_logs() -> None:
    mock_backend = MagicMock()
    mock_backend.query_logs.return_value = {
        "data": {
            "result": [
                {
                    "stream": {"service_name": "svc"},
                    "values": [["1000000", "info log"], ["2000000", "error in pipeline"]],
                }
            ]
        }
    }
    result = query_grafana_logs(service_name="svc", grafana_backend=mock_backend)
    assert result["available"] is True
    assert result["total_logs"] == 2
    assert len(result["error_logs"]) == 1
    assert result["summary"] == "2 log(s) for svc, 1 look like errors"


def test_run_returns_unavailable_when_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch(
        "integrations.grafana.tools._helpers.get_grafana_client_from_credentials",
        return_value=mock_client,
    ):
        result = query_grafana_logs(
            service_name="svc", grafana_endpoint="https://grafana.example.com"
        )
    assert result["available"] is False


def test_run_no_loki_datasource() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.loki_datasource_uid = None
    with patch(
        "integrations.grafana.tools._helpers.get_grafana_client_from_credentials",
        return_value=mock_client,
    ):
        result = query_grafana_logs(
            service_name="svc", grafana_endpoint="https://grafana.example.com"
        )
    assert result["available"] is False
    assert "Loki" in result["error"]


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.loki_datasource_uid = "loki-uid"
    mock_client.account_id = "acc-1"
    mock_client.query_loki.return_value = {
        "success": True,
        "logs": [{"message": "info log"}, {"message": "error crash"}],
        "total_logs": 2,
    }
    with patch(
        "integrations.grafana.tools._helpers.get_grafana_client_from_credentials",
        return_value=mock_client,
    ):
        result = query_grafana_logs(
            service_name="svc", grafana_endpoint="https://grafana.example.com"
        )
    assert result["available"] is True
    assert result["total_logs"] == 2
    assert len(result["error_logs"]) == 1
    assert result["summary"] == "2 log(s) for svc, 1 look like errors"


def test_run_fallback_to_pipeline_name() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.loki_datasource_uid = "loki-uid"
    mock_client.account_id = "acc-1"
    # First call returns empty, second (pipeline_name fallback) returns data
    mock_client.query_loki.side_effect = [
        {"success": True, "logs": []},
        {"success": True, "logs": [{"message": "pipeline log"}], "total_logs": 1},
    ]
    with patch(
        "integrations.grafana.tools._helpers.get_grafana_client_from_credentials",
        return_value=mock_client,
    ):
        result = query_grafana_logs(
            service_name="svc",
            pipeline_name="my-pipeline",
            grafana_endpoint="https://grafana.example.com",
        )
    assert result["available"] is True
