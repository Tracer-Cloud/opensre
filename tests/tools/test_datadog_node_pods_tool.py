"""Tests for DataDogNodePodsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.DataDogNodePodsTool import get_pods_on_node
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestDataDogNodePodsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_pods_on_node.__opensre_registered_tool__


def test_is_available_requires_connection_and_node_ip() -> None:
    rt = get_pods_on_node.__opensre_registered_tool__
    assert (
        rt.is_available({"datadog": {"connection_verified": True, "node_ip": "10.0.1.1"}}) is True
    )
    assert rt.is_available({"datadog": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_pods_on_node.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["node_ip"] == "10.0.1.42"
    assert params["api_key"] == "dd_api_key_test"


def test_run_returns_unavailable_when_no_node_ip() -> None:
    result = get_pods_on_node(node_ip="", api_key="key", app_key="akey")
    assert result["available"] is False


def test_run_returns_unavailable_when_no_client() -> None:
    result = get_pods_on_node(node_ip="10.0.1.1", api_key=None, app_key=None)
    assert result["available"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.get_pods_on_node.return_value = {
        "success": True,
        "pods": [{"pod_name": "my-pod", "namespace": "default"}],
        "total": 1,
    }
    with patch("app.tools.DataDogNodePodsTool.make_client", return_value=mock_client):
        result = get_pods_on_node(node_ip="10.0.1.1", api_key="key", app_key="akey")
    assert result["available"] is True
    assert result["node_ip"] == "10.0.1.1"
    assert result["total"] == 1


def test_run_api_error() -> None:
    mock_client = MagicMock()
    mock_client.get_pods_on_node.return_value = {"success": False, "error": "Forbidden"}
    with patch("app.tools.DataDogNodePodsTool.make_client", return_value=mock_client):
        result = get_pods_on_node(node_ip="10.0.1.1", api_key="key", app_key="akey")
    assert result["available"] is False
