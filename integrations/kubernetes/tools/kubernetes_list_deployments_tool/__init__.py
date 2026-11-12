"""Registry entrypoint for kubernetes_list_deployments."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_list_deployments_tool.tool import (
    KubernetesListDeploymentsTool,
    kubernetes_list_deployments,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesListDeploymentsTool", "kubernetes_list_deployments"]
