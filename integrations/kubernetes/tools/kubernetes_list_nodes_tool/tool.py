"""List Kubernetes cluster nodes with conditions and capacity."""

from __future__ import annotations

from typing import Any

import integrations.kubernetes.tools._shared as _shared
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable


class KubernetesListNodesTool(BaseTool):
    """List Kubernetes cluster nodes with conditions and capacity."""

    name = "kubernetes_list_nodes"
    source = "kubernetes"
    description = (
        "List all nodes in the Kubernetes cluster with their readiness conditions, "
        "CPU/memory capacity and allocatable resources, and taints. "
        "Use to diagnose node pressure, NotReady nodes, or scheduling issues."
    )
    use_cases = [
        "Finding nodes in NotReady or MemoryPressure/DiskPressure condition",
        "Checking available allocatable CPU and memory across nodes",
        "Identifying nodes with taints that prevent pod scheduling",
        "Correlating pod scheduling failures with node capacity",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = []
    injected_params = ["kubeconfig", "kubeconfig_path", "context"]
    input_schema = {
        "type": "object",
        "properties": {
            "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
            "kubeconfig_path": {
                "type": "string",
                "default": "",
                "description": "Path to kubeconfig file (alternative to kubeconfig)",
            },
            "context": {
                "type": "string",
                "default": "",
                "description": "Kubeconfig context to use",
            },
            "limit": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of nodes to return",
            },
        },
        "required": [],
    }
    outputs = {
        "nodes": "List of nodes with conditions, capacity, allocatable resources, and taints",
        "total": "Total number of nodes returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _shared.is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "limit": 50,
        }

    def run(
        self,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        limit: int = 50,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _shared.make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": "default",
                }
            }
        )
        if client is None:
            return _shared.missing_client_error({"nodes": [], "total": 0})
        with client:
            result = client.list_nodes(limit=limit)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), nodes=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "nodes": result["nodes"],
                "total": result["total"],
            }


kubernetes_list_nodes = KubernetesListNodesTool()


__all__ = ["KubernetesListNodesTool", "kubernetes_list_nodes"]
