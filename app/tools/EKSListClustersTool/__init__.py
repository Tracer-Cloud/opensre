"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from app.integrations.clients.eks.eks_client import EKSClient
from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


def _eks_available(sources: dict) -> bool:
    return bool(sources.get("eks", {}).get("connection_verified"))


def _eks_creds(eks: dict) -> dict:
    return {
        "role_arn": eks["role_arn"],
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
    }


class EKSListClustersTool(BaseTool):
    """List EKS clusters in the AWS account."""

    name = "list_eks_clusters"
    source = "eks"
    description = "List EKS clusters in the AWS account."
    use_cases = [
        "Discovering what EKS clusters exist in the account",
        "Confirming a cluster name before running other EKS actions",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "cluster_names": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return _eks_available(sources)

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_names": eks.get("cluster_names", []),
            **_eks_creds(eks),
        }

    def run(self, role_arn: str, external_id: str = "", region: str = "us-east-1", cluster_names: list | None = None, **_kwargs) -> dict:
        logger.info("[eks] list_eks_clusters role=%s region=%s", role_arn, region)
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            clusters = client.list_clusters()
            if cluster_names:
                clusters = [c for c in clusters if c in cluster_names]
            return {"source": "eks", "available": True, "clusters": clusters, "error": None}
        except ClientError as e:
            return {"source": "eks", "available": False, "clusters": [], "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "clusters": [], "error": str(e)}


list_eks_clusters = EKSListClustersTool()
