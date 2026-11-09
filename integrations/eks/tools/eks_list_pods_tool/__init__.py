"""List pods in an EKS namespace with status and restart counts — Kubernetes SDK backed."""

from __future__ import annotations

import logging
from typing import Any, cast

from pydantic import BaseModel, Field

from core.tool import EvidenceType, SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.eks.availability import eks_available_or_backend
from integrations.eks.eks_k8s_client import build_k8s_clients
from integrations.eks.workload_helper import extract_workload_params

logger = logging.getLogger(__name__)


class ListEKSPodsInput(BaseModel):
    cluster_name: str = Field(description="EKS cluster name.")
    namespace: str = Field(
        description="Kubernetes namespace to inspect, or `all` for every namespace."
    )
    region: str = Field(default="us-east-1", description="AWS region of the EKS cluster.")


class ListEKSPodsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether pod listing succeeded.")
    cluster_name: str | None = Field(default=None, description="Cluster queried.")
    namespace: str = Field(description="Namespace scope used for pod query.")
    total_pods: int = Field(default=0, description="Total number of pods discovered.")
    pods: list[dict[str, Any]] = Field(
        default_factory=list, description="All pod entries returned."
    )
    failing_pods: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Pods not in Running/Succeeded phases.",
    )
    high_restart_pods: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Pods with container restart count above threshold.",
    )
    error: str | None = Field(default=None, description="Error details when listing fails.")


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
    source_id="eks_core_v1",
    evidence_type=EvidenceType.TOPOLOGY,
    side_effect_level=SideEffectLevel.READ_ONLY,
    examples=[
        "List pods in `payments` namespace to identify CrashLoopBackOff pods.",
        "Use namespace `all` to detect widespread node scheduling issues.",
    ],
    anti_examples=["Use this tool to run kubectl exec or mutate Kubernetes resources."],
    input_model=ListEKSPodsInput,
    output_model=ListEKSPodsOutput,
    injected_params=("role_arn", "external_id", "credentials", "eks_backend"),
    is_available=eks_available_or_backend,
    extract_params=extract_workload_params,
)
def list_eks_pods(
    cluster_name: str,
    namespace: str,
    role_arn: str = "",
    external_id: str = "",
    region: str = "us-east-1",
    credentials: dict[str, Any] | None = None,
    eks_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List all pods in a namespace with their status, phase, restart counts, and conditions.

    When ``eks_backend`` is provided (e.g. a FixtureEKSBackend from the synthetic
    harness) the call short-circuits and returns the backend's response directly.
    """
    logger.info("[eks] list_eks_pods cluster=%s ns=%s", cluster_name, namespace)
    if eks_backend is not None:
        return cast(
            "dict[str, Any]",
            eks_backend.list_pods(cluster_name=cluster_name, namespace=namespace),
        )
    try:
        core_v1, _ = build_k8s_clients(
            cluster_name,
            role_arn,
            external_id,
            region,
            credentials=credentials,
        )
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
        report_run_error(
            e,
            tool_name="list_eks_pods",
            source="eks",
            component="integrations.eks.tools.eks_list_pods_tool",
            method="core_v1.list_namespaced_pod",
            logger=logger,
            extras={"cluster_name": cluster_name, "namespace": namespace, "region": region},
        )
        return tool_unavailable("eks", str(e), namespace=namespace)
