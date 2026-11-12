"""Registry entrypoint for kubernetes_list_services."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_services_tool.tool import (
    KubernetesListServicesTool,
    kubernetes_list_services,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListServicesTool", "kubernetes_list_services"]
