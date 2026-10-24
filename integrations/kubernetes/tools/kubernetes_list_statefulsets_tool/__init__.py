"""Registry entrypoint for kubernetes_list_statefulsets."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_statefulsets_tool.tool import (
    KubernetesListStatefulSetsTool,
    kubernetes_list_statefulsets,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListStatefulSetsTool", "kubernetes_list_statefulsets"]
