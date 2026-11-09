"""List EKS clusters in the AWS account — boto3 backed."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.eks.availability import eks_available
from integrations.eks.eks_client import EKSClient
from integrations.eks.workload_helper import extract_cluster_params

logger = logging.getLogger(__name__)


@tool(
    name="list_eks_clusters",
    source="eks",
    description="List EKS clusters in the AWS account.",
    use_cases=[
        "Discovering what EKS clusters exist in the account",
        "Confirming a cluster name before running other EKS actions",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "cluster_names": {"type": "array", "items": {"type": "string"}},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["role_arn"],
    },
    is_available=eks_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=extract_cluster_params,
)
def list_eks_clusters(
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    cluster_names: list | None = None,
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List EKS clusters in the AWS account."""
    logger.info("[eks] list_eks_clusters role=%s region=%s", role_arn, region)
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        clusters = client.list_clusters()
        if cluster_names:
            clusters = [c for c in clusters if c in cluster_names]
        return {"source": "eks", "available": True, "clusters": clusters, "error": None}
    except ClientError as e:
        report_run_error(
            e,
            tool_name="list_eks_clusters",
            source="eks",
            component="integrations.eks.tools.eks_list_clusters_tool",
            method="EKSClient.list_clusters",
            severity="warning",
            extras={"role_arn": role_arn, "region": region},
        )
        return tool_unavailable("eks", str(e), clusters=[])
    except Exception as e:
        report_run_error(
            e,
            tool_name="list_eks_clusters",
            source="eks",
            component="integrations.eks.tools.eks_list_clusters_tool",
            method="EKSClient.list_clusters",
            extras={"role_arn": role_arn, "region": region},
        )
        return tool_unavailable("eks", str(e), clusters=[])
