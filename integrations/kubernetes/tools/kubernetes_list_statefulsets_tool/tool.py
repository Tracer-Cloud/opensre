"""List Kubernetes StatefulSets with replica status."""

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


class KubernetesListStatefulSetsTool(BaseTool):
    """List Kubernetes StatefulSets with replica status."""

    name = "kubernetes_list_statefulsets"
    source = "kubernetes"
    description = (
        "List StatefulSets in a Kubernetes namespace with desired, ready, current, "
        "and updated replica counts. Use to detect degraded or stalled StatefulSet rollouts."
    )
    use_cases = [
        "Checking whether a StatefulSet has unavailable replicas",
        "Diagnosing stuck StatefulSet rolling updates",
        "Verifying database or stateful service replica health",
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
                "description": "Maximum number of StatefulSets to return",
            },
        },
        "required": [],
    }
    outputs = {
        "statefulsets": "List of StatefulSets with desired/ready/current/updated replica counts",
        "total": "Total number of StatefulSets returned",
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
            namespace, allowed_namespaces, {"statefulsets": [], "total": 0}
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
            return _shared.missing_client_error({"statefulsets": [], "total": 0})
        with client:
            result = client.list_statefulsets(namespace=namespace, limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), statefulsets=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "statefulsets": result["statefulsets"],
                "total": result["total"],
            }


kubernetes_list_statefulsets = KubernetesListStatefulSetsTool()


__all__ = ["KubernetesListStatefulSetsTool", "kubernetes_list_statefulsets"]
