"""Registry entrypoint for kubernetes_list_pods."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_pods_tool.tool import (
    KubernetesListPodsTool,
    kubernetes_list_pods,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListPodsTool", "kubernetes_list_pods"]
