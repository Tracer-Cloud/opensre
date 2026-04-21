"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _deployment_status_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("deployment"))


def _deployment_status_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks["cluster_name"],
        "namespace": eks.get("namespace", "default"),
        "deployment_name": eks["deployment"],
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_deployment_status",
    source="eks",
    description="Get EKS deployment rollout status — desired vs ready vs unavailable replicas.",
    use_cases=[
        "Checking if a deployment has unavailable replicas",
        "Verifying rollout status after a deployment change",
    ],
    requires=["cluster_name", "deployment_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "deployment_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "deployment_name", "role_arn"],
    },
    is_available=_deployment_status_is_available,
    extract_params=_deployment_status_extract_params,
)
def get_eks_deployment_status(
    cluster_name: str,
    namespace: str,
    deployment_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get EKS deployment rollout status — desired vs ready vs unavailable replicas."""
    logger.info(
        "[eks] get_eks_deployment_status cluster=%s ns=%s deployment=%s",
        cluster_name,
        namespace,
        deployment_name,
    )
    try:
        _, apps_v1 = build_k8s_clients(cluster_name, role_arn, external_id, region)
        dep = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        spec = dep.spec
        status = dep.status
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in (status.conditions or [])
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "deployment_name": deployment_name,
            "desired_replicas": spec.replicas,
            "ready_replicas": status.ready_replicas,
            "available_replicas": status.available_replicas,
            "unavailable_replicas": status.unavailable_replicas,
            "conditions": conditions,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] get_eks_deployment_status FAILED: %s", e, exc_info=True)
        return {
            "source": "eks",
            "available": False,
            "deployment_name": deployment_name,
            "error": str(e),
        }
