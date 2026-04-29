"""Unit tests for the shared EKS workload-params helper.

See: https://github.com/Tracer-Cloud/opensre/issues/895
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.tools._eks_helpers import extract_eks_workload_params


def _make_sources(**eks_fields) -> dict:
    return {
        "eks": {
            "cluster_name": "my-cluster",
            "role_arn": "arn:aws:iam::123:role/r",
            **eks_fields,
        }
    }


def test_extract_eks_workload_params_defaults() -> None:
    sources = _make_sources()
    params = extract_eks_workload_params(sources)
    assert params["cluster_name"] == "my-cluster"
    assert params["namespace"] == "all"
    assert params["eks_backend"] is None
    # Credential fields delegated to eks_creds must be present
    assert params["role_arn"] == "arn:aws:iam::123:role/r"
    assert params["external_id"] == ""
    assert params["region"] == "us-east-1"


def test_extract_eks_workload_params_explicit_namespace() -> None:
    sources = _make_sources(namespace="kube-system")
    params = extract_eks_workload_params(sources)
    assert params["namespace"] == "kube-system"


def test_extract_eks_workload_params_backend_passthrough() -> None:
    backend = MagicMock()
    sources = _make_sources(_backend=backend)
    params = extract_eks_workload_params(sources)
    assert params["eks_backend"] is backend


def test_extract_eks_workload_params_custom_default_namespace() -> None:
    sources = _make_sources()
    params = extract_eks_workload_params(sources, default_namespace="default")
    assert params["namespace"] == "default"


def test_extract_eks_workload_params_none_namespace_uses_default() -> None:
    sources = _make_sources(namespace=None)
    params = extract_eks_workload_params(sources)
    assert params["namespace"] == "all"


def test_extract_eks_workload_params_custom_region() -> None:
    sources = _make_sources(region="eu-west-1")
    params = extract_eks_workload_params(sources)
    assert params["region"] == "eu-west-1"


def test_extract_eks_workload_params_with_external_id() -> None:
    sources = _make_sources(external_id="my-ext-id-123")
    params = extract_eks_workload_params(sources)
    assert params["external_id"] == "my-ext-id-123"


def test_extract_eks_workload_params_missing_eks_key_raises_clear_error() -> None:
    """sources without 'eks' must raise KeyError with a descriptive message."""
    sources: dict = {}
    with pytest.raises(KeyError, match="'eks' key missing from sources dict"):
        extract_eks_workload_params(sources)
