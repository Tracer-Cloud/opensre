"""List Kubernetes DaemonSets with desired/ready/available counts."""

from __future__ import annotations

from typing import Any

import integrations.kubernetes.tools._shared as _shared
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.kubernetes.tools._namespace_allowlist import (
    effective_allowed_namespaces,
    namespace_access_error,
)


class KubernetesListDaemonSetsTool(BaseTool):
    """List Kubernetes DaemonSets with desired/ready/available counts."""

    name = "kubernetes_list_daemonsets"
    source = "kubernetes"
    description = (
        "List DaemonSets in a Kubernetes namespace with desired, current, ready, "
        "up-to-date, and available counts per node. "
        "Use to diagnose node-agent or logging/monitoring DaemonSet issues."
    )
    use_cases = [
        "Checking whether a DaemonSet is running on all expected nodes",
        "Diagnosing nodes where a DaemonSet pod is not scheduled or not ready",
        "Verifying a DaemonSet update has rolled out to all nodes",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of DaemonSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "daemonsets": "List of DaemonSets with desired/current/ready/up_to_date/available counts",
        "total": "Total number of DaemonSets returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _shared.is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "allowed_namespaces": effective_allowed_namespaces(k8s),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        limit: int = 50,
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace_error = namespace_access_error(
            namespace, allowed_namespaces, {"daemonsets": [], "total": 0}
        )
        if namespace_error is not None:
            return namespace_error
        client = _shared.make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _shared.missing_client_error({"daemonsets": [], "total": 0})
        with client:
            result = client.list_daemonsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), daemonsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "daemonsets": result["daemonsets"],
                "total": result["total"],
            }


kubernetes_list_daemonsets = KubernetesListDaemonSetsTool()


__all__ = ["KubernetesListDaemonSetsTool", "kubernetes_list_daemonsets"]
