"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging
from typing import Any

from app.services.eks.eks_k8s_client import build_k8s_clients
from app.tools.EKSListClustersTool import _eks_available, _eks_creds
from app.tools.tool_decorator import tool

logger = logging.getLogger(__name__)


def _list_pods_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    eks = sources["eks"]
    return {
        "cluster_name": eks["cluster_name"],
        "namespace": eks.get("namespace") or "all",
        **_eks_creds(eks),
    }


@tool(
    name="list_eks_pods",
    source="eks",
    description="List all pods in a namespace with their status, phase, restart counts, and conditions.",
    use_cases=[
        "Discovering what pods exist before fetching logs",
        "Finding which pods are crashing, pending, or failed",
        "Checking restart counts for crash-looping containers",
    ],
    requires=["cluster_name"],
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' to scan all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    },
    is_available=_eks_available,
    extract_params=_list_pods_extract_params,
)
def list_eks_pods(
    cluster_name: str,
    namespace: str,
    role_arn: str,
    external_id: str = "",
    region: str = "us-east-1",
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all pods in a namespace with their status, phase, restart counts, and conditions."""
    logger.info("[eks] list_eks_pods cluster=%s ns=%s", cluster_name, namespace)
    try:
        core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
        pod_list = (
            core_v1.list_pod_for_all_namespaces()
            if namespace == "all"
            else core_v1.list_namespaced_pod(namespace=namespace)
        )

        pods = []
        for pod in pod_list.items:
            containers = []
            for cs in pod.status.container_statuses or []:
                state = {}
                if cs.state.running:
                    state = {"running": True, "started_at": str(cs.state.running.started_at)}
                elif cs.state.waiting:
                    state = {
                        "waiting": True,
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                elif cs.state.terminated:
                    state = {
                        "terminated": True,
                        "exit_code": cs.state.terminated.exit_code,
                        "reason": cs.state.terminated.reason,
                        "message": cs.state.terminated.message,
                    }
                containers.append(
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "state": state,
                    }
                )
            conditions = [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
                for c in (pod.status.conditions or [])
            ]
            pods.append(
                {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "phase": pod.status.phase,
                    "node_name": pod.spec.node_name,
                    "containers": containers,
                    "conditions": conditions,
                    "start_time": str(pod.status.start_time),
                }
            )

        failing = [p for p in pods if p["phase"] not in ("Running", "Succeeded")]
        crashing = [p for p in pods if any(c["restart_count"] > 3 for c in p["containers"])]
        return {
            "source": "eks",
            "available": True,
            "cluster_name": cluster_name,
            "namespace": namespace,
            "total_pods": len(pods),
            "pods": pods,
            "failing_pods": failing,
            "high_restart_pods": crashing,
            "error": None,
        }
    except Exception as e:
        logger.error("[eks] list_eks_pods FAILED: %s", e, exc_info=True)
        return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}
