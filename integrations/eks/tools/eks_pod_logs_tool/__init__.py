"""Recent logs from one EKS pod — Kubernetes SDK backed."""

from __future__ import annotations

import logging
from typing import Any, cast

from core.tool import report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.eks.availability import eks_available_or_backend
from integrations.eks.eks_k8s_client import build_k8s_clients
from integrations.eks.workload_helper import eks_creds

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
        **eks_creds(eks),
    }


_EKS_POD_LOGS_ANTI = (
    "Do not call without exact cluster_name, namespace, and pod_name from list_eks_pods.",
    "Do not use for non-EKS kubeconfigs — use kubernetes_get_pod_logs instead.",
    "Do not use to exec, restart, or mutate workloads.",
)


@tool(
    name="get_eks_pod_logs",
    source="eks",
    description=(
        "Fetch recent logs from one EKS pod. Require absolute cluster_name, "
        "namespace, and pod_name (discover via list_eks_pods first)."
    ),
    use_cases=[
        "Fetching crash logs from a specific EKS pod",
        "Reviewing application output for a known failing pod",
    ],
    anti_examples=list(_EKS_POD_LOGS_ANTI),
    requires=["cluster_name", "pod_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {
                "type": "string",
                "description": "Exact EKS cluster name",
            },
            "namespace": {
                "type": "string",
                "description": "Kubernetes namespace of the pod",
            },
            "pod_name": {
                "type": "string",
                "description": "Exact pod name from list_eks_pods",
            },
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
            "credentials": {"type": ["object", "null"], "default": None},
            "tail_lines": {"type": "integer", "default": 100},
        },
        "required": ["cluster_name", "namespace", "pod_name", "role_arn"],
    },
    is_available=_pod_logs_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_pod_logs_extract_params,
)
def get_eks_pod_logs(
    cluster_name: str,
    namespace: str,
    pod_name: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
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
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
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
        report_run_error(
            e,
            tool_name="get_eks_pod_logs",
            source="eks",
            component="integrations.eks.tools.eks_pod_logs_tool",
            method="core_v1.read_namespaced_pod_log",
            logger=logger,
            extras={
                "cluster_name": cluster_name,
                "namespace": namespace,
                "pod_name": pod_name,
            },
        )
        return tool_unavailable("eks", str(e), pod_name=pod_name)
