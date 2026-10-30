"""Registry entrypoint for kubernetes_get_pod_logs."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_get_pod_logs_tool.tool import (
    KubernetesGetPodLogsTool,
    kubernetes_get_pod_logs,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesGetPodLogsTool", "kubernetes_get_pod_logs"]
