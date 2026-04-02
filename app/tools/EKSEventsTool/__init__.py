"""EKS workload investigation tools — Kubernetes Python SDK backed."""

from __future__ import annotations

import logging

from app.integrations.clients.eks.eks_k8s_client import build_k8s_clients
from app.tools.base import BaseTool
from app.tools.EKSListClustersTool import _eks_available, _eks_creds

logger = logging.getLogger(__name__)


class EKSEventsTool(BaseTool):
    """Get Kubernetes Warning events in a namespace."""

    name = "get_eks_events"
    source = "eks"
    description = "Get Kubernetes Warning events in a namespace."
    use_cases = [
        "Finding OOMKilled, FailedScheduling, BackOff, Unhealthy, FailedMount events",
        "Understanding what Kubernetes reported during an incident",
    ]
    requires = ["cluster_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string", "description": "Use 'all' for all namespaces"},
            "role_arn": {"type": "string"},
            "external_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": ["cluster_name", "namespace", "role_arn"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(_eks_available(sources) and sources.get("eks", {}).get("cluster_name"))

    def extract_params(self, sources: dict) -> dict:
        eks = sources["eks"]
        return {
            "cluster_name": eks["cluster_name"],
            "namespace": eks.get("namespace", "default"),
            **_eks_creds(eks),
        }

    def run(self, cluster_name: str, namespace: str, role_arn: str, external_id: str = "", region: str = "us-east-1", **_kwargs) -> dict:
        logger.info("[eks] get_eks_events cluster=%s ns=%s", cluster_name, namespace)
        try:
            core_v1, _ = build_k8s_clients(cluster_name, role_arn, external_id, region)
            event_list = core_v1.list_event_for_all_namespaces() if namespace == "all" else core_v1.list_namespaced_event(namespace=namespace)
            warning_events = [
                {
                    "namespace": e.metadata.namespace, "reason": e.reason, "message": e.message,
                    "type": e.type, "count": e.count,
                    "involved_object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "first_time": str(e.first_timestamp), "last_time": str(e.last_timestamp),
                }
                for e in event_list.items if e.type == "Warning"
            ]
            return {
                "source": "eks", "available": True, "cluster_name": cluster_name, "namespace": namespace,
                "warning_events": warning_events, "total_warning_count": len(warning_events), "error": None,
            }
        except Exception as e:
            logger.error("[eks] get_eks_events FAILED: %s", e, exc_info=True)
            return {"source": "eks", "available": False, "namespace": namespace, "error": str(e)}


get_eks_events = EKSEventsTool()
