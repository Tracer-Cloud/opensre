"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any, cast

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_creds
from app.tools.tool_decorator import tool
from app.tools.utils.availability import eks_available_or_backend

logger = logging.getLogger(__name__)


def _pod_logs_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("pod_name"))


def _pod_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace", "default"),
        "pod_name": eks.get("pod_name", ""),
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


@tool(
    name="get_eks_pod_logs",
    source="eks",
    description="Fetch logs from a specific EKS pod.",
    use_cases=[
        "Fetching crash logs from a specific pod",
        "Reviewing application output for a known failing pod",
    ],
    requires=["cluster_name", "pod_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "tail_lines": {"type": "integer", "default": 100},
        },
        "required": ["cluster_name", "namespace", "pod_name", "role_arn"],
    },
    is_available=_pod_logs_is_available,
    extract_params=_pod_logs_extract_params,
)
def get_eks_pod_logs(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    tail_lines: int = 100,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch logs from a specific EKS pod.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_pod_logs cluster=%s ns=%s pod=%s", cluster_name, namespace, pod_name)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_pod_logs(
                cluster_name=cluster_name, namespace=namespace, pod_name=pod_name
            ),
        )
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name, namespace=namespace, tail_lines=tail_lines
        )
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "pod_name": pod_name,
            "logs": logs,
            "error": None,
        }
    except Exception as e:
        logger.error(
            "[eks] get_eks_pod_logs failed cluster=%s pod=%s error=%s",
            cluster_name,
            pod_name,
            e,
            exc_info=True,
        )
        return {"source": "eks", "available": False, "pod_name": pod_name, "error": str(e)}
