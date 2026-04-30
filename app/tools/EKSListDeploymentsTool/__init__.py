"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any, cast

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.tool_decorator import tool
from app.tools.utils.availability import eks_available_or_backend
from app.tools.utils.eks_workload_helper import extract_workload_params

logger = logging.getLogger(__name__)


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
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    },
    is_available=eks_available_or_backend,
    extract_params=extract_workload_params,
)
def list_eks_deployments(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all deployments in a namespace with replica counts and availability status.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] list_eks_deployments cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.list_deployments(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        _, apps_v1 = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
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
