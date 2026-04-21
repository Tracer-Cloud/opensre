"""Tests for EKSDescribeClusterTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.tools.EKSDescribeClusterTool import describe_eks_cluster
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSDescribeClusterToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return describe_eks_cluster.__opensre_registered_tool__


def test_is_available_requires_cluster_name() -> None:
    rt = describe_eks_cluster.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True, "cluster_name": "c1"}}) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = describe_eks_cluster.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.describe_cluster.return_value = {
        "status": "ACTIVE",
        "version": "1.28",
        "endpoint": "https://abc.gr7.us-east-1.eks.amazonaws.com",
        "roleArn": "arn:aws:iam::123:role/cluster",
        "logging": {},
        "resourcesVpcConfig": {},
        "tags": {},
    }
    with patch("app.tools.EKSDescribeClusterTool.EKSClient", return_value=mock_client):
        result = describe_eks_cluster(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is True
    assert result["status"] == "ACTIVE"
    assert result["kubernetes_version"] == "1.28"


def test_run_handles_client_error() -> None:
    mock_client = MagicMock()
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}, "DescribeCluster"
    )
    mock_client.describe_cluster.side_effect = error
    with patch("app.tools.EKSDescribeClusterTool.EKSClient", return_value=mock_client):
        result = describe_eks_cluster(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is False
