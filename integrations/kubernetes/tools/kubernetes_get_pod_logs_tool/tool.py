"""Fetch recent log lines from a Kubernetes pod container."""

from __future__ import annotations

from typing import Any

import integrations.kubernetes.tools._shared as _shared
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.kubernetes.tools._namespace_allowlist import (
    effective_allowed_namespaces,
    namespace_access_error,
)


class KubernetesGetPodLogsTool(BaseTool):
    """Fetch recent log lines from a Kubernetes pod container."""

    name = "kubernetes_get_pod_logs"
    source = "kubernetes"
    description = (
        "Fetch recent log lines from one Kubernetes pod (optionally one container). "
        "Require the exact pod_name from kubernetes_list_pods; do not guess names."
    )
    use_cases = [
        "Reading application error logs from a crashing or misbehaving pod",
        "Diagnosing startup failures and misconfigurations via container logs",
        "Collecting evidence of OOM kills, panics, or stack traces",
    ]
    anti_examples = [
        "Do not call before kubernetes_list_pods when the pod name is unknown.",
        "Do not use for CloudWatch or Datadog log search — wrong backend.",
        "Do not use to exec into a pod or mutate cluster state.",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "pod_name": {
                "type": "string",
                "description": "Exact pod name (from kubernetes_list_pods)",
            },
            "container": {
                "type": "string",
                "default": "",
                "description": "Container name (required for multi-container pods)",
            },
            "tail_lines": {
                "type": "integer",
                "default": 100,
                "description": "Number of log lines to return from the end of the log",
            },
        },
        "required": ["pod_name"],
    }
    outputs = {
        "lines": "Log lines from the container",
        "total": "Number of log lines returned",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        return _shared.is_available(sources)

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        k8s = sources["kubernetes"]
        return {
            "kubeconfig": k8s.get("kubeconfig", ""),
            "kubeconfig_path": k8s.get("kubeconfig_path", ""),
            "context": k8s.get("context", ""),
            "namespace": k8s.get("namespace", "default"),
            "allowed_namespaces": effective_allowed_namespaces(k8s),
            "pod_name": k8s.get("pod_name", ""),
            "container": k8s.get("container", ""),
            "tail_lines": 100,
        }

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        container: str = "",
        tail_lines: int = 100,
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace_error = namespace_access_error(
            namespace, allowed_namespaces, {"lines": [], "total": 0}
        )
        if namespace_error is not None:
            return namespace_error
        if not pod_name:
            return tool_unavailable(
                "kubernetes",
                "pod_name is required; call kubernetes_list_pods first to find the pod name.",
                lines=[],
                total=0,
            )
        client = _shared.make_client(
            {
                "kubernetes": {
                    "kubeconfig": kubeconfig,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context,
                    "namespace": namespace,
                }
            }
        )
        if client is None:
            return _shared.missing_client_error({"lines": [], "total": 0})
        with client:
            result = client.get_pod_logs(
                namespace=namespace, pod_name=pod_name, container=container, tail_lines=tail_lines
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), lines=[], total=0
                )
            return {
                "source": "kubernetes",
                "available": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": result.get("container"),
                "lines": result["lines"],
                "total": result["total"],
            }


kubernetes_get_pod_logs = KubernetesGetPodLogsTool()


__all__ = ["KubernetesGetPodLogsTool", "kubernetes_get_pod_logs"]
