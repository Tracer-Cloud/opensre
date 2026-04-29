"""Tests for eks workload helpers"""

from __future__ import annotations

import pytest

from app.tools.utils.eks_workload_helper import extract_cluster_params, extract_workload_params


def test_extract_workload_params_basic_params():
    """Test basic parameter extraction with minimal config"""
    sources = {"eks": {"cluster_name": "test-cluster", "namespace": "default"}}

    result = extract_workload_params(sources)

    assert result["cluster_name"] == "test-cluster"
    assert result["namespace"] == "default"
    assert result["region"] == "us-east-1"
    assert result["role_arn"] == ""
    assert result["external_id"] == ""
    assert result["credentials"] is None


def test_extract_workload_params_namespace_defaults_to_all():
    """Test namespace defaults to 'all' when not provided"""
    sources = {"eks": {"cluster_name": "test-cluster"}}

    result = extract_workload_params(sources)

    assert result["namespace"] == "all"


def test_extract_workload_params_handles_all_optional_fields():
    """Test extraction includes all optional AWS fields"""
    sources = {
        "eks": {
            "cluster_name": "prod-cluster",
            "role_arn": "arn:aws:iam::123:role/test",
            "external_id": "external-123",
            "region": "us-west-2",
            "credentials": {"access_key": "key123"},
        }
    }

    result = extract_workload_params(sources)

    assert result["cluster_name"] == "prod-cluster"
    assert result["role_arn"] == "arn:aws:iam::123:role/test"
    assert result["external_id"] == "external-123"
    assert result["region"] == "us-west-2"
    assert result["credentials"] == {"access_key": "key123"}


def test_extract_workload_params_missing_eks_raises_error():
    """Test ValueError when 'eks' key is missin for workload extraction"""
    sources = {"other": {}}

    with pytest.raises(ValueError, match="must contain an 'eks' key"):
        extract_workload_params(sources)


def test_extract_cluster_params_extracts_cluster_names():
    """Test cluster names are extracted correctly"""
    sources = {"eks": {"cluster_names": ["cluster-1", "cluster-2"]}}

    result = extract_cluster_params(sources)

    assert result["cluster_names"] == ["cluster-1", "cluster-2"]


def test_extract_cluster_params_defaults_to_empty_list():
    """Test cluster_names defaults to empty list when not provided"""
    sources = {"eks": {}}

    result = extract_cluster_params(sources)

    assert result["cluster_names"] == []


def test_extract_cluster_params_includes_credentials():
    """Test credentials are included in cluster extraction"""
    sources = {
        "eks": {
            "cluster_names": ["test"],
            "role_arn": "arn:aws:iam::12356:role/test",
            "region": "us-west-2",
        }
    }

    result = extract_cluster_params(sources)

    assert result["role_arn"] == "arn:aws:iam::12356:role/test"
    assert result["region"] == "us-west-2"


def test_extract_cluster_params_missing_eks_raises_error():
    """Test ValueError when 'eks' key is missing for cluster extraction"""
    sources = {"other": {}}

    with pytest.raises(ValueError, match="must contain an 'eks' key"):
        extract_cluster_params(sources)
