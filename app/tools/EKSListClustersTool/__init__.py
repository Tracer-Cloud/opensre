"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from app.services.eks.eks_client import EKSClient
from app.tools.tool_decorator import tool
from app.tools.utils.eks_workload_helper import extract_cluster_params

logger = logging.getLogger(__name__)


def _eks_available(sources: dict[str, dict]) -> bool:
    return bool(sources.get("eks", {}).get("connection_verified"))


def _eks_creds(eks: dict) -> dict:
    return {
        "role_arn": eks.get("role_arn", ""),
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
        "credentials": eks.get("credentials"),
    }


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
    is_available=_eks_available,
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
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
    except Exception as e:
        return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
