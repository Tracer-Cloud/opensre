"""Tests for GrafanaAlertRulesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GrafanaAlertRulesTool import query_grafana_alert_rules
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaAlertRulesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_alert_rules.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_alert_rules.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_alert_rules.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_run_with_backend() -> None:
    mock_backend = MagicMock()
    mock_backend.query_alert_rules.return_value = {"groups": [{"name": "my-group"}]}
    result = query_grafana_alert_rules(grafana_backend=mock_backend)
    assert result["available"] is True
    assert "raw" in result


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.GrafanaAlertRulesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.return_value = [
        {"uid": "r1", "title": "High CPU", "state": "Firing"}
    ]
    with patch("app.tools.GrafanaAlertRulesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(grafana_endpoint="http://grafana")
    assert result["available"] is True
    assert result["total_rules"] == 1


def test_run_with_folder_filter() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.return_value = []
    with patch("app.tools.GrafanaAlertRulesTool._resolve_grafana_client", return_value=mock_client):
        result = query_grafana_alert_rules(folder="my-folder", grafana_endpoint="http://grafana")
    assert result["folder_filter"] == "my-folder"
    mock_client.query_alert_rules.assert_called_once_with(folder="my-folder")
