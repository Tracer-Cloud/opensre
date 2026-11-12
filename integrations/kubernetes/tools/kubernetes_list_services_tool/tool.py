"""List Kubernetes services with their type, ports, and selector."""

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


class KubernetesListServicesTool(BaseTool):
    """List Kubernetes services with their type, ports, and selector."""

    name = "kubernetes_list_services"
    source = "kubernetes"
    description = (
        "List services in a Kubernetes namespace with their type (ClusterIP/NodePort/LoadBalancer), "
        "clusterIP, external IPs, port mappings, and pod selector. "
        "Use to diagnose connectivity issues or verify service routing."
    )
    use_cases = [
        "Checking which pods a service routes to via its selector",
        "Verifying LoadBalancer external IP assignment",
        "Diagnosing port mismatches between services and pods",
        "Finding services exposed via NodePort for debugging",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "label_selector": {
                "type": "string",
                "default": "",
                "description": "Label selector filter (e.g. 'app=my-service')",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of services to return",
            },
        },
        "required": [],
    }
    outputs = {
        "services": "List of services with type, clusterIP, ports, and selector",
        "total": "Total number of services returned",
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
            "label_selector": "",
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        label_selector: str = "",
        limit: int = 50,
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace_error = namespace_access_error(
            namespace, allowed_namespaces, {"services": [], "total": 0}
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
            return _shared.missing_client_error({"services": [], "total": 0})
        with client:
            result = client.list_services(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), services=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "services": result["services"],
                "total": result["total"],
            }


kubernetes_list_services = KubernetesListServicesTool()


__all__ = ["KubernetesListServicesTool", "kubernetes_list_services"]
