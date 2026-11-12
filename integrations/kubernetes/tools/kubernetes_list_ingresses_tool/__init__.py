"""Registry entrypoint for kubernetes_list_ingresses."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_ingresses_tool.tool import (
    KubernetesListIngressesTool,
    kubernetes_list_ingresses,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListIngressesTool", "kubernetes_list_ingresses"]
