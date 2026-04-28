"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _list_ns_is_available(sources: dict[str, dict]) -> bool:
    return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))


def _list_ns_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {"cluster_name": eks["cluster_name"], **_eks_creds(eks)}


@tool(
    name="list_eks_namespaces",
    source="eks",
    description="List all namespaces in the EKS cluster with their status.",
    use_cases=[
        "Discovering what namespaces are present before querying pods/deployments",
        "Confirming an alert namespace actually exists in the cluster",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
        },
        "required": ["cluster_name", "role_arn"],
    },
    is_available=_list_ns_is_available,
    extract_params=_list_ns_extract_params,
)
def list_eks_namespaces(
    cluster_name: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all namespaces in the EKS cluster with their status."""
    logger.info("[eks] list_eks_namespaces cluster=%s", cluster_name)
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        ns_list = core_v1.list_namespace()
        namespaces = [
            {
                "name": ns.metadata.name,
                "status": ns.status.phase,
                "labels": ns.metadata.labels or {},
            }
            for ns in ns_list.items
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespaces": namespaces,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_namespaces FAILED: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "cluster_name": cluster_name, "error": str(e)}
