"""Tests for EKSDeploymentStatusTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSDeploymentStatusTool import get_eks_deployment_status
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSDeploymentStatusToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_eks_deployment_status.__opensre_registered_tool__


def test_is_available_requires_deployment() -> None:
    rt = get_eks_deployment_status.__opensre_registered_tool__
    assert rt.is_available({
        "eks": {"connection_verified": True, "cluster_name": "c1", "deployment": "my-dep"}
    }) is True
    assert rt.is_available({
        "eks": {"connection_verified": True, "cluster_name": "c1"}
    }) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_eks_deployment_status.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["deployment_name"] == "my-deployment"


def test_run_happy_path() -> None:
    mock_dep = MagicMock()
    mock_dep.spec.replicas = 3
    mock_dep.status.ready_replicas = 3
    mock_dep.status.available_replicas = 3
    mock_dep.status.unavailable_replicas = 0
    mock_dep.status.conditions = []
    mock_apps_v1 = MagicMock()
    mock_apps_v1.read_namespaced_deployment.return_value = mock_dep
    with patch("app.tools.EKSDeploymentStatusTool.build_k8s_clients", return_value=(MagicMock(), mock_apps_v1)):
        result = get_eks_deployment_status(
            cluster_name="c1",
            namespace="default",
            deployment_name="my-dep",
            role_arn="arn:aws:iam::123:role/r",
        )
    assert result["available"] is True
    assert result["desired_replicas"] == 3
    assert result["unavailable_replicas"] == 0


def test_run_handles_exception() -> None:
    with patch("app.tools.EKSDeploymentStatusTool.build_k8s_clients", side_effect=Exception("forbidden")):
        result = get_eks_deployment_status(
            cluster_name="c1",
            namespace="default",
            deployment_name="my-dep",
            role_arn="arn:aws:iam::123:role/r",
        )
    assert result["available"] is False
    assert "forbidden" in result["error"]
