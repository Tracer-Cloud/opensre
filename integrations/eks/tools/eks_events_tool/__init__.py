"""Kubernetes Warning events in an EKS namespace — Kubernetes SDK backed."""

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


def _events_is_available(sources: dict[str, dict]) -> bool:
    return bool(eks_available_or_backend(sources) and sources.get("eks", {}).get("cluster_name"))


def _events_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace", "default"),
        "eks_backend": eks.get("_backend"),
        **eks_creds(eks),
    }


@tool(
    name="get_eks_events",
    source="eks",
    description="Get Kubernetes Warning events in a namespace.",
    use_cases=[
        "Finding OOMKilled, FailedScheduling, BackOff, Unhealthy, FailedMount events",
        "Understanding what Kubernetes reported during an incident",
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
    is_available=_events_is_available,
    injected_params=("credentials", "external_id", "role_arn"),
    extract_params=_events_extract_params,
)
def get_eks_events(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get Kubernetes Warning events in a namespace.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] get_eks_events cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.get_events(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
        event_list = (
            core_v1.list_event_for_all_namespaces()
            if namespace == "all"
            else core_v1.list_namespaced_event(namespace=namespace)
        )
        warning_events = [
            {
                "namespace": e.metadata.namespace,
                "reason": e.reason,
                "message": e.message,
                "type": e.type,
                "count": e.count,
                "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "first_time": str(e.first_timestamp),
                "last_time": str(e.last_timestamp),
            }
            for e in event_list.items
            if e.type == "Warning"
        ]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "warning_events": warning_events,
            "total_warning_count": len(warning_events),
            "error": None,
        }
    except Exception as e:
        report_run_error(
            e,
            tool_name="get_eks_events",
            source="eks",
            component="integrations.eks.tools.eks_events_tool",
            method="core_v1.list_namespaced_event",
            logger=logger,
            extras={"cluster_name": cluster_name, "namespace": namespace},
        )
        return tool_unavailable("eks", str(e), namespace=namespace)
