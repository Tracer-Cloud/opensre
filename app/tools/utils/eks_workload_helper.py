"""Shared helpers for EKS workload investigation tools"""

from __future__ import annotations

from typing import Any


def _eks_creds(eks: dict) -> dict:
    """Extract AWS credentials from EKS source"""

    return {
        "role_arn": eks.get("role_arn", ""),
        "external_id": eks.get("external_id", ""),
        "region": eks.get("region", "us-east-1"),
        "credentials": eks.get("credentials"),
    }


def extract_workload_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract common parameters for workload list operations (pods/deployments)"""

    eks = sources.get("eks")
    if eks is None:
        raise ValueError("Sources dictionary must contain an 'eks' key with cluster configuration")

    return {
        "cluster_name": eks.get("cluster_name", ""),
        "namespace": eks.get("namespace") or "all",
        "eks_backend": eks.get("_backend"),
        **_eks_creds(eks),
    }


def extract_cluster_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract parameters for cluster list operation"""
    eks = sources.get("eks")
    if eks is None:
        raise ValueError("Sources dictionary must contain an 'eks' key with cluster configuration")

    return {
        "cluster_names": eks.get("cluster_names", []),
        **_eks_creds(eks),
    }
