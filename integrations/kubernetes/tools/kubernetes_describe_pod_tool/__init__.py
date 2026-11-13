"""Registry entrypoint for kubernetes_describe_pod."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_describe_pod_tool.tool import (
    KubernetesDescribePodTool,
    kubernetes_describe_pod,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesDescribePodTool", "kubernetes_describe_pod"]
