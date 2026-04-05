"""Tests for EKSEventsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSEventsTool import get_eks_events
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSEventsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_eks_events.__opensre_registered_tool__


def test_is_available_requires_cluster_name() -> None:
    rt = get_eks_events.__opensre_registered_tool__
    assert rt.is_available({
        "eks": {"connection_verified": True, "cluster_name": "c1"}
    }) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_eks_events.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["namespace"] == "default"


def _make_event(reason: str, message: str, event_type: str = "Warning") -> MagicMock:
    evt = MagicMock()
    evt.metadata.namespace = "default"
    evt.reason = reason
    evt.message = message
    evt.type = event_type
    evt.count = 1
    evt.involved_object.kind = "Pod"
    evt.involved_object.name = "my-pod"
    evt.first_timestamp = "2024-01-01T00:00:00Z"
    evt.last_timestamp = "2024-01-01T00:01:00Z"
    return evt


def test_run_happy_path() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespaced_event.return_value = MagicMock(items=[
        _make_event("OOMKilled", "Container was OOM killed"),
        _make_event("Pulled", "Image pulled", event_type="Normal"),
    ])
    with patch("app.tools.EKSEventsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())):
        result = get_eks_events(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is True
    # Only Warning events are included
    assert result["total_warning_count"] == 1
    assert result["warning_events"][0]["reason"] == "OOMKilled"


def test_run_all_namespaces() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_event_for_all_namespaces.return_value = MagicMock(items=[])
    with patch("app.tools.EKSEventsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())):
        result = get_eks_events(
            cluster_name="c1", namespace="all", role_arn="arn:aws:iam::123:role/r"
        )
    mock_core_v1.list_event_for_all_namespaces.assert_called_once()
    assert result["available"] is True


def test_run_handles_exception() -> None:
    with patch("app.tools.EKSEventsTool.build_k8s_clients", side_effect=Exception("api error")):
        result = get_eks_events(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is False
    assert "api error" in result["error"]
