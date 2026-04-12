"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _list_deployments_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks["cluster_name"],
        "namespace": eks.get("namespace") or "all",
        **_eks_creds(eks),
    }


@tool(
    name="list_eks_deployments",
    source="eks",
    description="List all deployments in a namespace with replica counts and availability status.",
    use_cases=[
        "Discovering what deployments exist and which are degraded/unavailable",
        "Scanning all namespaces for degraded deployments",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    },
    is_available=_eks_available,
    extract_params=_list_deployments_extract_params,
)
def list_eks_deployments(
    cluster_name: str,
    namespace: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all deployments in a namespace with replica counts and availability status."""
    logger.info("[eks] list_eks_deployments cluster=%s ns=%s", cluster_name, namespace)
    try:
        _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)
        dep_list = (
            apps_v1.list_deployment_for_all_namespaces()
            if namespace == "all"
            else apps_v1.list_namespaced_deployment(namespace=namespace)
        )
        deployments = []
        for dep in dep_list.items:
            status = dep.status
            desired = dep.spec.replicas or 0
            ready = status.ready_replicas or 0
            unavailable = status.unavailable_replicas or 0
            deployments.append(
                {
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "desired": desired,
                    "ready": ready,
                    "available": status.available_replicas or 0,
                    "unavailable": unavailable,
                    "degraded": unavailable > 0 or ready < desired,
                }
            )
        degraded = [d for d in deployments if d["degraded"]]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_deployments": len(deployments),
            "deployments": deployments,
            "degraded_deployments": degraded,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_deployments FAILED: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}
