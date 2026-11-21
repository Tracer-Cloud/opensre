"""Registry entrypoint for kubernetes_list_configmaps."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_configmaps_tool.tool import (
    KubernetesListConfigMapsTool,
    kubernetes_list_configmaps,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListConfigMapsTool", "kubernetes_list_configmaps"]
