"""Availability checks for EKS tools.

The synthetic harnesses under ``tests/synthetic/`` inject a fixture
``_backend`` object via the integration source dict so tools can run
against mocks. Both checks live here so vendor tools share one
consistent notion of "EKS is usable" instead of re-deriving it per tool.
"""

from __future__ import annotations


def eks_available(sources: dict[str, dict]) -> bool:
    """Available only when real, connection-verified EKS credentials are present.

    The narrower of the two checks: tools whose body always talks to AWS or the
    Kubernetes API use this, because a fixture backend cannot serve them.
    """
    return bool(sources.get("eks", {}).get("connection_verified"))


def eks_available_or_backend(sources: dict[str, dict]) -> bool:
    """Available when real EKS credentials are present OR a fixture backend is injected.

    Used by EKS tool wrappers whose ``extract_params`` can delegate to a
    mock ``eks_backend`` for synthetic tests. Tools without backend
    support use the narrower :func:`eks_available` instead.

    The ``_backend`` slot is reserved for fixture backends that implement
    the EKS tool API (``list_pods``, ``get_pod_logs``, ...). Other backend
    types that speak different protocols should be placed in their own
    distinct source slots and are invisible to this check — the real EKS
    tools stay deactivated for those modes.
    """
    eks = sources.get("eks", {})
    return bool(eks.get("connection_verified") or eks.get("_backend"))
