"""List pods in a Kubernetes namespace to diagnose availability and restart issues."""

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


class KubernetesListPodsTool(BaseTool):
    """List pods in a Kubernetes namespace to diagnose availability and restart issues."""

    name = "kubernetes_list_pods"
    source = "kubernetes"
    description = (
        "List pods in a Kubernetes namespace. Returns pod phase, container readiness, "
        "restart counts, and node assignment. Use to detect crash-looping or pending pods."
    )
    use_cases = [
        "Checking if pods are in a crash-loop or pending state",
        "Identifying which pods are not ready or have high restart counts",
        "Filtering pods by label selector to scope investigation",
        "Verifying that a deployment's pods are running after a rollout",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["kubeconfig"]
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
                "description": "Maximum number of pods to return",
            },
        },
        "required": [],
    }
    outputs = {
        "pods": "List of pods with phase, container statuses, and node assignment",
        "total": "Total number of pods returned",
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
            namespace, allowed_namespaces, {"pods": [], "total": 0}
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
            return _shared.missing_client_error({"pods": [], "total": 0})
        with client:
            result = client.list_pods(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), pods=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "pods": result["pods"],
                "total": result["total"],
            }


kubernetes_list_pods = KubernetesListPodsTool()


__all__ = ["KubernetesListPodsTool", "kubernetes_list_pods"]
