"""Namespace allowlist checks shared by the Kubernetes tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.tool_framework.utils import tool_access_denied
from integrations.kubernetes.client import _RESOURCE_DISPATCH


def effective_allowed_namespaces(k8s: dict[str, Any]) -> tuple[str, ...]:
    default_namespace = k8s.get("namespace") or "default"
    return tuple(dict.fromkeys((default_namespace, *(k8s.get("allowed_namespaces") or ()))))


def is_cluster_scoped(resource_type: str) -> bool:
    key = resource_type.lower()
    entry = _RESOURCE_DISPATCH.get(key) or _RESOURCE_DISPATCH.get(key.rstrip("s"))
    return bool(entry and entry[2])


def namespace_access_error(
    namespace: str,
    allowed_namespaces: Iterable[str],
    extra: dict[str, Any],
) -> dict[str, Any] | None:
    allowed = sorted(set(allowed_namespaces))
    if namespace in allowed:
        return None
    return tool_access_denied(
        "kubernetes",
        f'namespace "{namespace}" is not in the configured allowlist '
        f"(allowed: {', '.join(allowed)})",
        **extra,
    )


__all__ = ["effective_allowed_namespaces", "is_cluster_scoped", "namespace_access_error"]
