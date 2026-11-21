"""Registry entrypoint for kubernetes_list_daemonsets."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_daemonsets_tool.tool import (
    KubernetesListDaemonSetsTool,
    kubernetes_list_daemonsets,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListDaemonSetsTool", "kubernetes_list_daemonsets"]
