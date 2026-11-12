"""Client construction and schema pieces shared by the Kubernetes tools."""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils import tool_unavailable
from integrations.config_models import KubernetesIntegrationConfig
from integrations.kubernetes.client import _RESOURCE_DISPATCH, KubernetesClient

RESOURCE_TYPE_ENUM: list[str] = sorted(_RESOURCE_DISPATCH.keys())


def make_client(sources: dict[str, Any]) -> KubernetesClient | None:
    k8s = sources.get("kubernetes", {})
    kubeconfig = k8s.get("kubeconfig", "")
    kubeconfig_path = k8s.get("kubeconfig_path", "")
    if not kubeconfig and not kubeconfig_path:
        return None
    try:
        cfg = KubernetesIntegrationConfig.model_validate(
            {
                "kubeconfig": kubeconfig,
                "kubeconfig_path": kubeconfig_path,
                "context": k8s.get("context", ""),
                "namespace": k8s.get("namespace", "default"),
            }
        )
        return KubernetesClient(cfg)
    except Exception:
        return None


def is_available(sources: dict[str, Any]) -> bool:
    k8s = sources.get("kubernetes", {})
    return bool(k8s.get("kubeconfig") or k8s.get("kubeconfig_path"))


def missing_client_error(extra: dict[str, Any]) -> dict[str, Any]:
    return tool_unavailable(
        "kubernetes", "Kubernetes integration is not configured (missing kubeconfig).", **extra
    )


SHARED_KUBECONFIG_PROPS: dict[str, Any] = {
    "kubeconfig": {"type": "string", "description": "Raw kubeconfig YAML string"},
    "kubeconfig_path": {
        "type": "string",
        "default": "",
        "description": "Path to kubeconfig file (alternative to kubeconfig)",
    },
    "context": {"type": "string", "default": "", "description": "Kubeconfig context to use"},
    "namespace": {
        "type": "string",
        "description": (
            "Kubernetes namespace to target. Omit to use the configured namespace; "
            "only namespaces in the configured allowlist are accepted."
        ),
    },
}


__all__ = [
    "RESOURCE_TYPE_ENUM",
    "SHARED_KUBECONFIG_PROPS",
    "is_available",
    "make_client",
    "missing_client_error",
]
