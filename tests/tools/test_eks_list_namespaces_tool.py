"""Tests for EKSListNamespacesTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSListNamespacesTool import list_eks_namespaces
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSListNamespacesToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_eks_namespaces.__opensre_registered_tool__


def test_is_available_requires_cluster_name() -> None:
    rt = list_eks_namespaces.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True, "cluster_name": "c1"}}) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_eks_namespaces.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"


def _make_ns(name: str, phase: str = "Active") -> MagicMock:
    ns = MagicMock()
    ns.metadata.name = name
    ns.metadata.labels = {}
    ns.status.phase = phase
    return ns


def test_run_happy_path() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespace.return_value = MagicMock(
        items=[_make_ns("default"), _make_ns("kube-system")]
    )
    with patch(
        "app.tools.EKSListNamespacesTool.build_k8s_clients",
        return_value=(mock_core_v1, MagicMock()),
    ):
        result = list_eks_namespaces(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is True
    assert len(result["namespaces"]) == 2
    assert any(ns["name"] == "default" for ns in result["namespaces"])


def test_run_handles_exception() -> None:
    with patch(
        "app.tools.EKSListNamespacesTool.build_k8s_clients", side_effect=Exception("api error")
    ):
        result = list_eks_namespaces(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is False
