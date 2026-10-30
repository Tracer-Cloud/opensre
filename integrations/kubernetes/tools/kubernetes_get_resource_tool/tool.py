"""Fetch a single named Kubernetes resource by type and name."""

from __future__ import annotations

from typing import Any

import integrations.kubernetes.tools._shared as _shared
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from integrations.kubernetes.tools._namespace_allowlist import (
    effective_allowed_namespaces,
    is_cluster_scoped,
    namespace_access_error,
)


class KubernetesGetResourceTool(BaseTool):
    """Fetch a single named Kubernetes resource by type and name."""

    name = "kubernetes_get_resource"
    source = "kubernetes"
    description = (
        "Fetch the raw spec and status of a single named Kubernetes resource, "
        "equivalent to `kubectl get <type> <name> -o json`. Supports: pod, deployment, "
        "statefulset, daemonset, service, ingress, configmap, replicaset, "
        "persistentvolumeclaim (pvc), and node. Env variable values are redacted (keys "
        "only) for pod and workload types (deployment, statefulset, daemonset, "
        "replicaset), same as kubernetes_describe_pod. For POD detail specifically, "
        "prefer kubernetes_describe_pod instead — it returns the same redacted data in "
        "a curated, investigation-shaped view (container states, owner references) "
        "rather than the raw API object. The namespace field is ignored for "
        "cluster-scoped types like node."
    )
    use_cases = [
        "Fetching the full YAML-equivalent of any non-pod named resource for deep inspection",
        "Reading a specific deployment's full spec including strategy and selector",
        "Inspecting a PVC's storage class, capacity, and bound status",
        "Getting the full node spec to check kubelet version and OS image",
    ]
    anti_examples = [
        "Inspecting a pod's containers, conditions, or env var keys (use "
        + "kubernetes_describe_pod — same detail, redacts env values)",
        "Listing multiple resources or filtering by label/field selector (use the "
        + "matching kubernetes_list_* tool instead)",
    ]
    surfaces = (ToolSurface.CHAT,)
    requires = ["resource_type", "name"]
    injected_params = ["kubeconfig", "kubeconfig_path", "context", "allowed_namespaces"]
    input_schema = {
        "type": "object",
        "properties": {
            **_shared.SHARED_KUBECONFIG_PROPS,
            "resource_type": {
                "type": "string",
                "enum": _shared.RESOURCE_TYPE_ENUM,
                "description": "Kubernetes resource type to fetch.",
            },
            "name": {
                "type": "string",
                "description": "Name of the resource to fetch",
            },
        },
        "required": ["resource_type", "name"],
    }
    outputs = {
        "resource": "Full resource object as a dict (equivalent to kubectl get -o json)",
        "resource_type": "The resource type that was fetched",
        "name": "The resource name that was fetched",
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
            "resource_type": k8s.get("resource_type", ""),
            "name": k8s.get("name", ""),
        }

    def run(
        self,
        resource_type: str,
        name: str,
        kubeconfig: str = "",
        kubeconfig_path: str = "",
        context: str = "",
        namespace: str = "default",
        allowed_namespaces: tuple[str, ...] = ("default",),
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not is_cluster_scoped(resource_type):
            namespace_error = namespace_access_error(
                namespace,
                allowed_namespaces,
                {"resource": {}, "resource_type": resource_type, "name": name},
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
            return _shared.missing_client_error(
                {"resource": {}, "resource_type": resource_type, "name": name}
            )
        with client:
            result = client.get_resource(
                resource_type=resource_type, name=name, namespace=namespace
            )
            if not result.get("success"):
                return tool_unavailable(
                    "kubernetes",
                    result.get("error", "unknown error"),
                    resource={},
                    resource_type=resource_type,
                    name=name,
                )
            return {
                "source": "kubernetes",
                "available": True,
                "resource_type": result["resource_type"],
                "name": result["name"],
                "resource": result["resource"],
            }


kubernetes_get_resource = KubernetesGetResourceTool()


__all__ = ["KubernetesGetResourceTool", "kubernetes_get_resource"]
