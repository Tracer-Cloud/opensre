"""List Kubernetes events for a namespace to diagnose crash loops and scheduling failures."""

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


class KubernetesGetEventsTool(BaseTool):
    """List Kubernetes events for a namespace to diagnose crash loops and scheduling failures."""

    name = "kubernetes_get_events"
    source = "kubernetes"
    description = (
        "List Kubernetes events for a namespace. Events capture crash loops, "
        "OOM kills, image pull failures, and scheduling issues. "
        "Use field_selector to scope events to a specific pod or deployment."
    )
    use_cases = [
        "Diagnosing crash-loop back-off by reading Warning events for a pod",
        "Detecting OOM kills and image pull failures from cluster events",
        "Understanding scheduling failures (Insufficient CPU/Memory)",
        "Correlating event timestamps with incident timeline",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "field_selector": {
                "type": "string",
                "default": "",
                "description": (
                    "Field selector to filter events "
                    "(e.g. 'involvedObject.name=my-pod,type=Warning')"
                ),
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of events to return",
            },
        },
        "required": [],
    }
    outputs = {
        "events": "List of events with reason, message, involved object, and timestamps",
        "total": "Total number of events returned",
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
            "field_selector": "",
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        field_selector: str = "",
        limit: int = 50,
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace_error = namespace_access_error(
            namespace, allowed_namespaces, {"events": [], "total": 0}
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
            return _shared.missing_client_error({"events": [], "total": 0})
        with client:
            result = client.get_events(
                namespace=namespace, field_selector=field_selector, limit=limit
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), events=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "namespace": namespace,
                "events": result["events"],
                "total": result["total"],
            }


kubernetes_get_events = KubernetesGetEventsTool()


__all__ = ["KubernetesGetEventsTool", "kubernetes_get_events"]
