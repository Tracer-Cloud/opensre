"""Tests for EKSNodeHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSNodeHealthTool import get_eks_node_health
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSNodeHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_eks_node_health.__opensre_registered_tool__


def test_is_available_requires_cluster_name() -> None:
    rt = get_eks_node_health.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True, "cluster_name": "c1"}}) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_eks_node_health.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["role_arn"] == "arn:aws:iam::123456789012:role/eks-role"


def _make_node(name: str, ready: str = "True") -> MagicMock:
    node = MagicMock()
    node.metadata.name = name
    node.metadata.labels = {"node.kubernetes.io/instance-type": "m5.large"}
    node.status.conditions = [
        MagicMock(type="Ready", status=ready),
        MagicMock(type="MemoryPressure", status="False"),
        MagicMock(type="DiskPressure", status="False"),
        MagicMock(type="PIDPressure", status="False"),
    ]
    node.status.capacity = {"cpu": "4", "memory": "16Gi"}
    node.status.allocatable = {"cpu": "3.9", "memory": "15Gi"}
    node.status.addresses = [MagicMock(type="InternalIP", address="10.0.1.1")]
    return node


def test_run_happy_path() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_node.return_value = MagicMock(
        items=[_make_node("node-1"), _make_node("node-2")]
    )
    with patch(
        "app.tools.EKSNodeHealthTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())
    ):
        result = get_eks_node_health(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is True
    assert result["total_nodes"] == 2
    assert result["not_ready_count"] == 0


def test_run_detects_not_ready_nodes() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_node.return_value = MagicMock(
        items=[_make_node("node-1", "True"), _make_node("node-2", "False")]
    )
    with patch(
        "app.tools.EKSNodeHealthTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())
    ):
        result = get_eks_node_health(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["not_ready_count"] == 1


def test_run_handles_exception() -> None:
    with patch(
        "app.tools.EKSNodeHealthTool.build_k8s_clients", side_effect=Exception("auth error")
    ):
        result = get_eks_node_health(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is False
    assert "auth error" in result["error"]
