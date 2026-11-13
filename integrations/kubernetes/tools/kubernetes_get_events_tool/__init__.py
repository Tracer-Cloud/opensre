"""Registry entrypoint for kubernetes_get_events."""

from __future__ import annotations

from integrations.kubernetes.tools.kubernetes_get_events_tool.tool import (
    KubernetesGetEventsTool,
    kubernetes_get_events,
)

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "KubernetesGetEventsTool", "kubernetes_get_events"]
