"""Registry entrypoint for kubernetes_list_nodes."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_nodes_tool.tool import (
    KubernetesListNodesTool,
    kubernetes_list_nodes,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListNodesTool", "kubernetes_list_nodes"]
