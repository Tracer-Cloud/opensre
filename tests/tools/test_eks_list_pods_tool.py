"""Tests for EKSListPodsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSListPodsTool import list_eks_pods
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSListPodsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_eks_pods.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = list_eks_pods.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True}}) is True
    assert rt.is_available({"eks": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_eks_pods.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["namespace"] == "default"


def _make_pod(name: str, phase: str = "Running", restart_count: int = 0) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.status.phase = phase
    pod.spec.node_name = "node-1"
    pod.status.start_time = "2024-01-01T00:00:00Z"
    cs = MagicMock()
    cs.name = "main"
    cs.ready = True
    cs.restart_count = restart_count
    cs.state.running = MagicMock()
    cs.state.running.started_at = "2024-01-01"
    cs.state.waiting = None
    cs.state.terminated = None
    pod.status.container_statuses = [cs]
    pod.status.conditions = []
    return pod


def test_run_happy_path() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespaced_pod.return_value = MagicMock(
        items=[_make_pod("pod-1"), _make_pod("pod-2")]
    )
    with patch(
        "app.tools.EKSListPodsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())
    ):
        result = list_eks_pods(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is True
    assert result["total_pods"] == 2
    assert result["failing_pods"] == []


def test_run_detects_crashing_pods() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespaced_pod.return_value = MagicMock(
        items=[_make_pod("pod-1", restart_count=10)]
    )
    with patch(
        "app.tools.EKSListPodsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())
    ):
        result = list_eks_pods(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert len(result["high_restart_pods"]) == 1


def test_run_all_namespaces() -> None:
    mock_core_v1 = MagicMock()
    mock_core_v1.list_pod_for_all_namespaces.return_value = MagicMock(items=[])
    with patch(
        "app.tools.EKSListPodsTool.build_k8s_clients", return_value=(mock_core_v1, MagicMock())
    ):
        result = list_eks_pods(
            cluster_name="c1", namespace="all", role_arn="arn:aws:iam::123:role/r"
        )
    mock_core_v1.list_pod_for_all_namespaces.assert_called_once()
    assert result["available"] is True


def test_run_handles_exception() -> None:
    with patch("app.tools.EKSListPodsTool.build_k8s_clients", side_effect=Exception("auth error")):
        result = list_eks_pods(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is False
