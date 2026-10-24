"""Registry entrypoint for kubernetes_get_resource."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_get_resource_tool.tool import (
    KubernetesGetResourceTool,
    kubernetes_get_resource,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesGetResourceTool", "kubernetes_get_resource"]
