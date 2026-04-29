"""Tests for eks workload helpers"""

from __future__ import annotations

from app.tools.utils.eks_workload_helper import extract_workload_params


def test_extract_basic_params():
    """Test basic parameter extraction with minimal config"""
    sources = {"eks": {"cluster_name": "test-cluster", "namespace": "default"}}

    result = extract_workload_params(sources)

    assert result["cluster_name"] == "test-cluster"
    assert result["namespace"] == "default"
    assert result["region"] == "us-east-1"
    assert result["role_arn"] == ""
    assert result["external_id"] == ""
    assert result["credentials"] is None


def test_namespace_defaults_to_all():
    """Test namespace defaults to 'all' when not provided"""
    sources = {"eks": {"cluster_name": "test-cluster"}}

    result = extract_workload_params(sources)

    assert result["namespace"] == "all"


def test_handles_all_optional_fields():
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


def test_missing_eks_raises_error():
    """Test ValueError when 'eks' key is missing"""
    sources = {"other": {}}

    try:
        extract_workload_params(sources)
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        assert "must contain an 'eks' key" in str(e)
