"""Shared helpers for EKS workload-listing tools.

Extracted from ``EKSListPodsTool`` and ``EKSListDeploymentsTool`` to remove
repeated credential-unpacking and ``eks_backend`` short-circuit boilerplate.

See: https://github.com/Tracer-Cloud/opensre/issues/895
"""

from __future__ import annotations

from typing import Any

from app.tools.EKSListClustersTool import _eks_creds
from app.tools.utils.availability import eks_available_or_backend

__all__ = [
    "eks_available_or_backend",
    "extract_eks_workload_params",
]


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
    - credential fields from :func:`~app.tools.EKSListClustersTool._eks_creds`
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

    Example
    -------
    >>> params = extract_eks_workload_params(sources)
    >>> list_eks_pods(**params)
    """
    eks = sources["eks"]
    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace") or default_namespace,
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }
