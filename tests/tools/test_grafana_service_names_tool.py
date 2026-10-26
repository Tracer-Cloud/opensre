"""Tests for GrafanaServiceNamesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from integrations.grafana.tools import query_grafana_service_names
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaServiceNamesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_service_names.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_service_names.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_service_names.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["grafana_endpoint"] == "https://grafana.example.com"


def test_run_with_backend() -> None:
    mock_backend = MagicMock()
    result = query_grafana_service_names(grafana_backend=mock_backend)
    assert result["available"] is True
    assert result["service_names"] == []


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_service_names(grafana_endpoint="http://grafana")
    assert result["available"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_loki_label_values.return_value = ["svc-a", "svc-b"]
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_service_names(grafana_endpoint="http://grafana")
    assert result["available"] is True
    assert result["service_names"] == ["svc-a", "svc-b"]
    mock_client.query_loki_label_values.assert_called_once_with("service_name")


@pytest.mark.parametrize(
    ("api_key", "username", "password"),
    [
        (None, "admin", "secret"),
        ("glsa_test", "", ""),
        (None, "", ""),
    ],
    ids=("basic-auth", "api-key", "anonymous"),
)
def test_run_forwards_auth_to_client(
    api_key: str | None,
    username: str,
    password: str,
) -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ) as resolve:
        query_grafana_service_names(
            grafana_endpoint="http://grafana",
            grafana_api_key=api_key,
            grafana_username=username,
            grafana_password=password,
        )

    resolve.assert_called_once_with("http://grafana", api_key, username, password, True, "")
