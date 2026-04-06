"""Tests for EKSListDeploymentsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.EKSListDeploymentsTool import list_eks_deployments
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSListDeploymentsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_eks_deployments.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = list_eks_deployments.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True}}) is True
    assert rt.is_available({"eks": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_eks_deployments.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"


def _make_deployment(name: str, desired: int = 3, ready: int = 3, unavailable: int = 0) -> MagicMock:
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = "default"
    dep.spec.replicas = desired
    dep.status.ready_replicas = ready
    dep.status.available_replicas = ready
    dep.status.unavailable_replicas = unavailable
    return dep


def test_run_happy_path() -> None:
    mock_apps_v1 = MagicMock()
    mock_apps_v1.list_namespaced_deployment.return_value = MagicMock(
        items=[_make_deployment("dep-1"), _make_deployment("dep-2")]
    )
    with patch("app.tools.EKSListDeploymentsTool.build_k8s_clients", return_value=(MagicMock(), mock_apps_v1)):
        result = list_eks_deployments(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is True
    assert result["total_deployments"] == 2
    assert result["degraded_deployments"] == []


def test_run_detects_degraded() -> None:
    mock_apps_v1 = MagicMock()
    mock_apps_v1.list_namespaced_deployment.return_value = MagicMock(
        items=[_make_deployment("dep-1", desired=3, ready=2, unavailable=1)]
    )
    with patch("app.tools.EKSListDeploymentsTool.build_k8s_clients", return_value=(MagicMock(), mock_apps_v1)):
        result = list_eks_deployments(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert len(result["degraded_deployments"]) == 1


def test_run_all_namespaces() -> None:
    mock_apps_v1 = MagicMock()
    mock_apps_v1.list_deployment_for_all_namespaces.return_value = MagicMock(items=[])
    with patch("app.tools.EKSListDeploymentsTool.build_k8s_clients", return_value=(MagicMock(), mock_apps_v1)):
        result = list_eks_deployments(
            cluster_name="c1", namespace="all", role_arn="arn:aws:iam::123:role/r"
        )
    mock_apps_v1.list_deployment_for_all_namespaces.assert_called_once()
    assert result["available"] is True


def test_run_handles_exception() -> None:
    with patch("app.tools.EKSListDeploymentsTool.build_k8s_clients", side_effect=Exception("forbidden")):
        result = list_eks_deployments(
            cluster_name="c1", namespace="default", role_arn="arn:aws:iam::123:role/r"
        )
    assert result["available"] is False
