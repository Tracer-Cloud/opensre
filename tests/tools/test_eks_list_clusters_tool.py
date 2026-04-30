"""Tests for EKSListClustersTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.tools.EKSListClustersTool import list_eks_clusters
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSListClustersToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_eks_clusters.__opensre_registered_tool__


def test_is_available_requires_connection_verified() -> None:
    rt = list_eks_clusters.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True}}) is True
    assert rt.is_available({"eks": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_eks_clusters.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["role_arn"] == "arn:aws:iam::123456789012:role/eks-role"


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.list_clusters.return_value = ["cluster-1", "cluster-2"]
    with patch("app.tools.EKSListClustersTool.EKSClient", return_value=mock_client):
        result = list_eks_clusters(role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is True
    assert result["clusters"] == ["cluster-1", "cluster-2"]


def test_run_with_cluster_filter() -> None:
    mock_client = MagicMock()
    mock_client.list_clusters.return_value = ["cluster-1", "cluster-2", "cluster-3"]
    with patch("app.tools.EKSListClustersTool.EKSClient", return_value=mock_client):
        result = list_eks_clusters(role_arn="arn:aws:iam::123:role/r", cluster_names=["cluster-1"])
    assert result["clusters"] == ["cluster-1"]


def test_run_handles_client_error() -> None:
    mock_client = MagicMock()
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "ListClusters")
    mock_client.list_clusters.side_effect = error
    with patch("app.tools.EKSListClustersTool.EKSClient", return_value=mock_client):
        result = list_eks_clusters(role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is False
    assert result["clusters"] == []


def test_run_forwards_credentials_to_eks_client() -> None:
    """Stored AWS-integration credentials must thread through into ``EKSClient``.

    Without this the `list_eks_clusters` path (the cluster-discovery /
    connection-verification step) would still hit ``sts.assume_role(RoleArn="", ...)``
    for IAM-user-only integrations and raise ``ParamValidationError``.
    """
    mock_client = MagicMock()
    mock_client.list_clusters.return_value = ["cluster-1"]
    creds = {
        "access_key_id": "AKIA_TEST",
        "secret_access_key": "SECRET",
        "session_token": "",
    }
    with patch("app.tools.EKSListClustersTool.EKSClient", return_value=mock_client) as cls:
        list_eks_clusters(role_arn="", credentials=creds)
    cls.assert_called_once()
    assert cls.call_args.kwargs["credentials"] == creds
    assert cls.call_args.kwargs["role_arn"] == ""


def test_run_credentials_none_by_default() -> None:
    """Existing role-based callers must keep working — credentials defaults to None."""
    mock_client = MagicMock()
    mock_client.list_clusters.return_value = []
    with patch("app.tools.EKSListClustersTool.EKSClient", return_value=mock_client) as cls:
        list_eks_clusters(role_arn="arn:aws:iam::123:role/r")
    assert cls.call_args.kwargs["credentials"] is None
