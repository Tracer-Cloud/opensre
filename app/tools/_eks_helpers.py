"""Shared helpers for EKS workload-listing tools.

Extracted from ``EKSListPodsTool`` and ``EKSListDeploymentsTool`` to remove
repeated credential-unpacking and ``eks_backend`` short-circuit boilerplate.

See: https://github.com/Tracer-Cloud/opensre/issues/895
"""

from __future__ import annotations

from typing import Any

from app.tools.utils.availability import eks_available_or_backend

__all__ = [
    "eks_available_or_backend",
    "eks_creds",
    "extract_eks_workload_params",
]


def eks_creds(eks: dict) -> dict:
    """Unpack the three credential fields shared by every EKS tool.

    Parameters
    ----------
    eks:
        The ``sources["eks"]`` sub-dict.

    Returns
    -------
    dict
        Flat dict with ``role_arn``, ``external_id``, and ``region`` keys.
    """
    return {
        "role_arn": eks.get("role_arn", ""),
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
    }


# Keep the private alias so EKSListClustersTool can import it without
# changing its internal call sites (_eks_creds is used there directly).
_eks_creds = eks_creds


def extract_eks_workload_params(
    sources: dict[str, dict],
    *,
    default_namespace: str = "all",
) -> dict[str, Any]:
    """Unpack EKS credential and workload routing fields from *sources*.

    Both ``EKSListPodsTool`` and ``EKSListDeploymentsTool`` require the same
    set of parameters from ``sources["eks"]``:

    - ``cluster_name`` — name of the EKS cluster
    - ``namespace`` — Kubernetes namespace (defaults to ``"all"``)
    - ``eks_backend`` — optional synthetic backend for testing / short-circuit
    - credential fields from :func:`eks_creds`
      (``role_arn``, ``external_id``, ``region``)

    Parameters
    ----------
    sources:
        The full sources dict passed to the tool's ``extract_params`` hook.
    default_namespace:
        Fallback namespace when the caller does not supply one.
        Defaults to ``"all"`` (scan every namespace).

    Returns
    -------
    dict[str, Any]
        A flat dict ready to be spread as ``**kwargs`` into the tool function.

    Raises
    ------
    KeyError
        If ``sources`` does not contain an ``"eks"`` key, a clear error is
        raised rather than letting the bare dict access produce an opaque
        ``KeyError`` deep in the call chain.

    Example
    -------
    >>> params = extract_eks_workload_params(sources)
    >>> list_eks_pods(**params)
    """
    eks = sources.get("eks")
    if eks is None:
        raise KeyError(
            "extract_eks_workload_params: 'eks' key missing from sources dict. "
            "Ensure the EKS integration is configured and sources['eks'] is set."
        )
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace") or default_namespace,
        "eks_backend": eks.get("_backend"),
        **eks_creds(eks),
    }
