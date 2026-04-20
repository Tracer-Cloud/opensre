"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any, cast

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_creds
from app.tools.tool_decorator import tool
from app.tools.utils.availability import eks_available_or_backend

logger = logging.getLogger(__name__)


def _node_health_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("cluster_name"))


def _node_health_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_node_health",
    source="eks",
    description="Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts.",
    use_cases=[
        "Investigating when pods are unschedulable or nodes are NotReady",
        "Checking memory/disk pressure on nodes",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_node_health_is_available,
    extract_params=_node_health_extract_params,
)
def get_eks_node_health(
    cluster_name: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get health status of all EKS nodes — conditions, capacity, allocatable, pod counts.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_node_health cluster=%s", cluster_name)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_node_health(cluster_name=cluster_name),
        )
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
        nodes = core_v1.list_node()
        node_health = []
        for node in nodes.items:
            conditions = {c.type: c.status for c in (node.status.conditions or [])}
            capacity = node.status.capacity or {}
            allocatable = node.status.allocatable or {}
            addresses = {a.type: a.address for a in (node.status.addresses or [])}
            node_health.append(
                {
                    "name": node.metadata.name,
                    "internal_ip": addresses.get("InternalIP"),
                    "ready": conditions.get("Ready"),
                    "memory_pressure": conditions.get("MemoryPressure"),
                    "disk_pressure": conditions.get("DiskPressure"),
                    "pid_pressure": conditions.get("PIDPressure"),
                    "capacity_cpu": capacity.get("cpu"),
                    "capacity_memory": capacity.get("memory"),
                    "allocatable_cpu": allocatable.get("cpu"),
                    "allocatable_memory": allocatable.get("memory"),
                    "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type")
                    if node.metadata.labels
                    else None,
                }
            )
        not_ready = sum(1 for n in node_health if n["ready"] != "True")
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "nodes": node_health,
            "total_nodes": len(node_health),
            "not_ready_count": not_ready,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_node_health FAILED: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
