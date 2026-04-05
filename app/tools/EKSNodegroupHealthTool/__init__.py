"""EKS cluster-level investigation tools — boto3 backed."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from app.services.eks.eks_client import EKSClient
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool


def _nodegroup_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _nodegroup_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}


@tool(
    name="get_eks_nodegroup_health",
    source="eks",
    description="Get EKS node group health — instance types, scaling config, AMI version, health issues.",
    use_cases=[
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking node capacity and scaling configuration",
        "Finding AMI version issues in EKS node groups",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "nodegroup_name": {"type": "string"},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_nodegroup_is_available,
    extract_params=_nodegroup_extract_params,
)
def get_eks_nodegroup_health(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    nodegroup_name: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get EKS node group health — instance types, scaling config, AMI version, health issues."""
    try:
        client = EKSClient(role_arn=role_arn, external_id=external_id, region=region)
        nodegroups = [nodegroup_name] if nodegroup_name else client.list_nodegroups(cluster_name)
        results = []
        for ng in nodegroups:
            ng_data = client.describe_nodegroup(cluster_name, ng)
            results.append({
                "name": ng, "status": ng_data.get("status"),
                "instance_types": ng_data.get("instanceTypes", []),
                "scaling_config": ng_data.get("scalingConfig", {}),
                "release_version": ng_data.get("releaseVersion"),
                "health": ng_data.get("health", {}), "node_role": ng_data.get("nodeRole"),
                "labels": ng_data.get("labels", {}), "taints": ng_data.get("taints", []),
            })
        return {"source": "eks", "available": True, "cluster_name": cluster_name, "nodegroups": results, "error": None}
    except ClientError as e:
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
    except Exception as e:
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
