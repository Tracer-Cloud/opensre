"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from app.integrations.clients.eks.eks_client import EKSClient
from app.tools.base import BaseTool
from app.tools.EKSListClustersTool import _eks_available, _eks_creds

logger = logging.getLogger(__name__)


class EKSDescribeClusterTool(BaseTool):
    """Describe an EKS cluster — health, version, status, endpoint, logging config."""

    name = "describe_eks_cluster"
    source = "eks"
    description = "Describe an EKS cluster — health, version, status, endpoint, logging config."
    use_cases = [
        "Investigating cluster-level issues: version mismatches, endpoint problems",
        "Checking if control plane logging is disabled",
        "Verifying cluster status (ACTIVE, DEGRADED, FAILED)",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}

    def run(self, cluster_name: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] describe_eks_cluster cluster=%s region=%s", cluster_name, region)
        try:
            client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
            cluster = client.describe_cluster(cluster_name)
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name,
                "status": cluster.get("status"), "kubernetes_version": cluster.get("version"),
                "endpoint": cluster.get("endpoint"), "cluster_role_arn": cluster.get("roleArn"),
                "logging": cluster.get("logging", {}), "resources_vpc_config": cluster.get("resourcesVpcConfig", {}),
                "tags": cluster.get("tags", {}), "error": None,
            }
        except ClientError as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
        except Exception as e:
            return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}


describe_eks_cluster = EKSDescribeClusterTool()
