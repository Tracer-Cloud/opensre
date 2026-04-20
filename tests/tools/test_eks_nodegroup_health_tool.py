"""Tests for EKSNodegroupHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from app.tools.EKSNodegroupHealthTool import get_eks_nodegroup_health
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestEKSNodegroupHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_eks_nodegroup_health.__opensre_registered_tool__


def test_is_available_requires_cluster_name() -> None:
    rt = get_eks_nodegroup_health.__opensre_registered_tool__
    assert rt.is_available({"eks": {"connection_verified": True, "cluster_name": "c1"}}) is True
    assert rt.is_available({"eks": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_eks_nodegroup_health.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["cluster_name"] == "my-cluster"


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.list_nodegroups.return_value = ["ng-1"]
    mock_client.describe_nodegroup.return_value = {
        "status": "ACTIVE",
        "instanceTypes": ["m5.large"],
        "scalingConfig": {"minSize": 1, "maxSize": 5, "desiredSize": 3},
        "releaseVersion": "1.28.5-20240101",
        "health": {},
        "nodeRole": "arn:aws:iam::123:role/ng",
        "labels": {},
        "taints": [],
    }
    with patch("app.tools.EKSNodegroupHealthTool.EKSClient", return_value=mock_client):
        result = get_eks_nodegroup_health(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is True
    assert len(result["nodegroups"]) == 1
    assert result["nodegroups"][0]["status"] == "ACTIVE"


def test_run_specific_nodegroup() -> None:
    mock_client = MagicMock()
    mock_client.describe_nodegroup.return_value = {
        "status": "ACTIVE",
        "instanceTypes": ["t3.medium"],
        "scalingConfig": {},
        "releaseVersion": "1.28",
        "health": {},
        "nodeRole": "arn:aws:iam::123:role/ng",
        "labels": {},
        "taints": [],
    }
    with patch("app.tools.EKSNodegroupHealthTool.EKSClient", return_value=mock_client):
        result = get_eks_nodegroup_health(
            cluster_name="c1", role_arn="arn:aws:iam::123:role/r", nodegroup_name="ng-specific"
        )
    assert result["available"] is True
    mock_client.list_nodegroups.assert_not_called()


def test_run_handles_client_error() -> None:
    mock_client = MagicMock()
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "ListNodegroups")
    mock_client.list_nodegroups.side_effect = error
    with patch("app.tools.EKSNodegroupHealthTool.EKSClient", return_value=mock_client):
        result = get_eks_nodegroup_health(cluster_name="c1", role_arn="arn:aws:iam::123:role/r")
    assert result["available"] is False
