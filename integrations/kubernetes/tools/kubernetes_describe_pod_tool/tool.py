"""Fetch full spec, status, and container states for a single Kubernetes pod."""

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


class KubernetesDescribePodTool(BaseTool):
    """Fetch full spec, status, and container states for a single Kubernetes pod."""

    name = "kubernetes_describe_pod"
    source = "kubernetes"
    description = (
        "Fetch the full spec and status for a single pod: containers, images, resource "
        "requests/limits, environment variables (values redacted, keys only), volume "
        "mounts, conditions, container states, and owner references. Use when list_pods "
        "shows a problem and you need deeper detail on one pod. Preferred over "
        "kubernetes_get_resource for pods specifically — both redact env values "
        "identically, but this tool returns a curated, investigation-shaped view "
        "(container states, owner references) rather than the raw API object. For any "
        "resource type other than pod, use kubernetes_get_resource instead."
    )
    use_cases = [
        "Inspecting container image versions and resource limits on a specific pod",
        "Diagnosing why a pod is stuck in Pending or Init state via detailed conditions",
        "Identifying owner (Deployment, StatefulSet, Job) of a pod",
        "Checking environment variable names (keys only) injected into a container — values are redacted",
    ]
    anti_examples = [
        "Fetching a non-pod resource such as a deployment, service, or node (use "
        + "kubernetes_get_resource)",
        "Listing many pods at once (use kubernetes_list_pods)",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["pod_name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "pod_name": {"type": "string", "description": "Name of the pod to describe"},
        },
        "required": ["pod_name"],
    }
    outputs = {
        "spec": "Pod spec including containers, volumes, node selector, and tolerations",
        "status": "Pod status including phase, conditions, and per-container states",
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
        }

    def run(
        self,
        pod_name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        namespace_error = namespace_access_error(
            namespace, allowed_namespaces, {"spec": {}, "status": {}}
        )
        if namespace_error is not None:
            return namespace_error
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
            return _shared.missing_client_error({"spec": {}, "status": {}})
        with client:
            result = client.describe_pod(namespace=namespace, pod_name=pod_name)
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes", result.get("error", "unknown error"), spec={}, status={}
                )
            return {
                "source": "kubernetes",
                "available": True,
                **{k: v for k, v in result.items() if k != "success"},
            }


kubernetes_describe_pod = KubernetesDescribePodTool()


__all__ = ["KubernetesDescribePodTool", "kubernetes_describe_pod"]
