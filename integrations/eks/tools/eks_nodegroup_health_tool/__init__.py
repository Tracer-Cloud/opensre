"""Managed node group health, scaling config, and AMI version — boto3 backed."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.eks.availability import eks_available
from integrations.eks.eks_client import EKSClient
from integrations.eks.workload_helper import eks_creds


def _nodegroup_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _nodegroup_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **eks_creds(eks)}


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
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_nodegroup_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_nodegroup_extract_params,
)
def get_eks_nodegroup_health(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    nodegroup_name: str | None = None,
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get EKS node group health — instance types, scaling config, AMI version, health issues."""
    # Track which nodegroup is being processed so a mid-loop failure can be
    # tagged with the actual failing name rather than the (possibly None)
    # caller-supplied input — matches the per-resource extras used by the
    # other migrated EKS tools (e.g. ``addon_name``, ``pod_name``).
    current_nodegroup: str | None = nodegroup_name
    try:
        client = EKSClient(
            role_arn=role_arn,
            external_id=external_id,
            region=region,
            credentials=credentials,
        )
        nodegroups = [nodegroup_name] if nodegroup_name else client.list_nodegroups(cluster_name)
        results = []
        for ng in nodegroups:
            current_nodegroup = ng
            ng_data = client.describe_nodegroup(cluster_name, ng)
            results.append(
                {
                    "name": ng,
                    "status": ng_data.get("status"),
                    "instance_types": ng_data.get("instanceTypes", []),
                    "scaling_config": ng_data.get("scalingConfig", {}),
                    "release_version": ng_data.get("releaseVersion"),
                    "health": ng_data.get("health", {}),
                    "node_role": ng_data.get("nodeRole"),
                    "labels": ng_data.get("labels", {}),
                    "taints": ng_data.get("taints", []),
                }
            )
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodegroups": results,
            "error": None,
        }
    except ClientError as e:
        report_run_error(
            e,
            tool_name="get_eks_nodegroup_health",
            source="eks",
            component="integrations.eks.tools.eks_nodegroup_health_tool",
            method="EKSClient.describe_nodegroup",
            severity="warning",
            extras={
                "cluster_name": cluster_name,
                "region": region,
                "nodegroup_name": current_nodegroup,
            },
        )
        return tool_unavailable("eks", str(e), cluster_name=cluster_name)
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_nodegroup_health",
            source="eks",
            component="integrations.eks.tools.eks_nodegroup_health_tool",
            method="EKSClient.describe_nodegroup",
            extras={
                "cluster_name": cluster_name,
                "region": region,
                "nodegroup_name": current_nodegroup,
            },
        )
        return tool_unavailable("eks", str(e), cluster_name=cluster_name)
