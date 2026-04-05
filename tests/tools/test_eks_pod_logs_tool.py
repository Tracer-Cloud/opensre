"""Tests for EKSPodLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSPodLogsTool import get_eks_pod_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSPodLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_eks_pod_logs.__opensre_registered_tool__


def test_is_available_requires_cluster_and_pod() -> None:
    rt = get_eks_pod_logs.__opensre_registered_tool__
    assert rt.is_available({
        "eks": {"connection_verified": True, "cluster_name": "c1", "pod_name": "pod-1"}
    }) is True
    assert rt.is_available({
        "eks": {"connection_verified": True, "cluster_name": "c1"}
    }) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_eks_pod_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["pod_name"] == "my-pod-abc"
    assert params["namespace"] == "default"


def test_run_happy_path() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.read_namespaced_pod_log.return_value = "line1\nline2\n"
    with patch("app.tools.EKSPodLogsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())):
        result = get_eks_pod_logs(
            cluster_name="c1", namespace="default", pod_name="pod-1", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is True
    assert result["logs"] == "line1\nline2\n"
    assert result["pod_name"] == "pod-1"


def test_run_handles_exception() -> None:
    with patch("app.tools.EKSPodLogsTool.build_k8s_clients", side_effect=Exception("k8s error")):
        result = get_eks_pod_logs(
            cluster_name="c1", namespace="default", pod_name="pod-1", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is False
    assert "k8s error" in result["error"]
