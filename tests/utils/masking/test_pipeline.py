"""Integration tests for the full masking pipeline.

Tests end-to-end masking workflow from planning through diagnosis
to final output, verifying:
1. Sensitive identifiers are masked before LLM calls
2. Placeholders are validated in LLM responses
3. Original values are restored for user-facing output
4. No placeholders leak to external APIs
"""

import os
from typing import Any

import pytest

from app.utils.masking import (
    MaskingContext,
    MaskingPolicy,
    count_error_issues,
    mask_dict,
    mask_text,
    should_panic,
    summarize_issues,
    unmask_dict,
    unmask_text,
    validate_placeholders,
)


class TestFullMaskingPipeline:
    """End-to-end tests for the complete masking workflow."""

    def test_pipeline_with_realistic_alert_data(self) -> None:
        """Test complete workflow with realistic investigation data."""
        # Create context with default policy
        ctx = MaskingContext.create()

        # Simulate realistic alert data with sensitive identifiers
        available_sources = {
            "eks": {
                "cluster_name": "prod-eks-cluster-us-east-1",
                "namespace": "payment-service",
                "pod_name": "payment-api-7d8f9b2c1-x4k9p",
                "region": "us-east-1",
            },
            "datadog": {
                "pipeline_name": "payments-prod",
                "service_name": "payment-api.example.com",
                "default_query": "service:payment-api status:error",
                "site": "datadoghq.com",
            },
            "grafana": {
                "service_name": "payment-api.internal",
                "pipeline_name": "payments",
                "loki_only": False,
            },
            "s3": {
                "bucket": "prod-data-lake-123456789012",
                "key": "transactions/2024/01/01/data.parquet",
                "prefix": "transactions/",
            },
        }

        # Step 1: Mask available sources (planning phase)
        masked_sources = mask_dict(available_sources, ctx)

        # Verify sensitive values are masked
        assert "prod-eks-cluster-us-east-1" not in str(masked_sources)
        assert "<CLUSTER_0>" in str(masked_sources)
        assert "payment-api.example.com" not in str(masked_sources)
        assert "<HOSTNAME_0>" in str(masked_sources)
        assert "123456789012" not in str(masked_sources)
        assert "<ACCOUNT_0>" in str(masked_sources)

        # Step 2: Simulate LLM planning prompt with masked data
        planning_prompt = f"""
        Available sources:
        EKS Cluster: {masked_sources["eks"]["cluster_name"]}
        Datadog Service: {masked_sources["datadog"]["service_name"]}
        S3 Bucket: {masked_sources["s3"]["bucket"]}

        Task: Select investigation actions.
        """

        # Verify prompt is masked
        assert "prod-eks-cluster-us-east-1" not in planning_prompt
        assert "<CLUSTER_0>" in planning_prompt

        # Step 3: Simulate LLM response with placeholder references
        llm_response = """
        Plan: Query logs from <CLUSTER_0> cluster.
        Check service health for <HOSTNAME_0>.
        Verify data in <ACCOUNT_0> bucket.
        """

        # Step 4: Validate LLM response
        issues = validate_placeholders(llm_response, ctx.placeholder_map)
        assert len(issues) == 0, f"Unexpected validation issues: {issues}"

        # Step 5: Unmask for user-facing output
        final_output = unmask_text(llm_response, ctx)

        # Verify original values are restored
        assert "prod-eks-cluster-us-east-1" in final_output
        assert "payment-api.example.com" in final_output
        assert "123456789012" in final_output
        assert "<CLUSTER_0>" not in final_output
        assert "<HOSTNAME_0>" not in final_output

    def test_pipeline_with_broken_placeholders_triggers_panic(self) -> None:
        """Test panic mode activates with excessive broken placeholders."""
        # Create context with low panic threshold
        policy = MaskingPolicy(
            mask_cluster_names=True,
            panic_threshold=3,
            validate_output=True,
        )
        ctx = MaskingContext.create(policy)

        # Mask some initial data to populate mappings
        _ = ctx.mask_text("Error in prod-cluster-01")

        # Simulate LLM response with many broken placeholders
        broken_response = """
        Analysis shows issues with <CLUSTER_0> and <_1> and <<HOSTNAME_0>>
        and <SERVICE_0abc> and unclosed <CLUSTER_1
        """

        # Validate response
        issues = validate_placeholders(broken_response, ctx.placeholder_map)

        # Should detect multiple errors
        error_count = count_error_issues(issues)
        assert error_count >= 4  # _1, <<HOSTNAME_0>>, SERVICE_0abc, unclosed

        # Panic threshold should be exceeded
        assert should_panic(issues, policy.panic_threshold)

        # Summary should show error breakdown
        summary = summarize_issues(issues)
        assert summary["ERROR"] >= 4

    def test_pipeline_placeholder_leakage_detection(self) -> None:
        """Test detection of placeholders that weren't in original mapping."""
        ctx = MaskingContext.create()

        # Mask some data
        _ = ctx.mask_text("Error in cluster-prod-01")
        _ = ctx.mask_text("Service api.example.com failed")

        # LLM hallucinates a new placeholder not in our mapping
        hallucinated_response = """
        Found issues with <CLUSTER_0> and <CLUSTER_999>
        Also check <HOSTNAME_0> and <SERVICE_5>
        """

        # Validate - should detect unknown placeholders
        issues = validate_placeholders(hallucinated_response, ctx.placeholder_map)

        # Filter for unknown placeholder warnings
        unknown_issues = [i for i in issues if "not in mapping" in i.message]
        assert len(unknown_issues) == 2  # CLUSTER_999 and SERVICE_5

    def test_pipeline_memory_limits(self) -> None:
        """Test placeholder map respects memory limits."""
        # Create context with small limit
        policy = MaskingPolicy(max_placeholders=5)
        ctx = MaskingContext.create(policy)

        # Add many identifiers exceeding limit
        for i in range(10):
            text = f"Error in cluster-prod-{i:03d}.example-{i}.com"
            _ = ctx.mask_text(text)

        # Map should be at capacity
        assert ctx.placeholder_map.get_size() == 5

        # Stats should show correct information
        stats = ctx.placeholder_map.get_stats()
        assert stats["size"] == 5
        assert stats["max_size"] == 5
        assert stats["remaining"] == 0

        # Additional identifiers should pass through unchanged
        overflow_text = "Final cluster-overflow-999 passes through"
        result = ctx.mask_text(overflow_text)
        assert "cluster-overflow-999" in result  # Not masked due to limit

    def test_pipeline_with_nested_evidence(self) -> None:
        """Test masking with complex nested evidence structure."""
        ctx = MaskingContext.create()

        # Complex nested evidence structure
        evidence = {
            "failed_jobs": [
                {
                    "job_name": "etl-prod-192.168.1.50",
                    "status_reason": "OOM in cluster-prod-01",
                    "host": "ip-192-168-1-50.ec2.internal",
                }
            ],
            "error_logs": [
                {
                    "message": "Connection to db-master.prod.internal:5432 failed",
                    "service": "payments-api.prod.svc.cluster.local",
                }
            ],
            "datadog_logs": [
                {
                    "host": "prod-k8s-node-192-168-1-100",
                    "service": "payment-service",
                    "message": "Request to api.partner.com failed",
                }
            ],
        }

        # Mask entire evidence structure
        masked_evidence = mask_dict(evidence, ctx)

        # Verify all sensitive data masked
        masked_str = str(masked_evidence)
        assert "192.168.1.50" not in masked_str
        assert "192-168-1-50" not in masked_str
        assert "cluster-prod-01" not in masked_str
        assert "db-master.prod.internal" not in masked_str
        assert "api.partner.com" not in masked_str

        # Verify placeholders present
        assert "<IP_" in masked_str or "<CLUSTER_" in masked_str

        # Unmask and verify restoration
        restored = unmask_dict(masked_evidence, ctx)
        assert restored == evidence

    def test_pipeline_validation_disabled(self) -> None:
        """Test that validation can be disabled via policy."""
        policy = MaskingPolicy(validate_output=False)
        ctx = MaskingContext.create(policy)

        # Populate mapping
        _ = ctx.mask_text("Error in cluster-prod-01")

        # Response with broken placeholder
        response = "Check <CLUSTER_0> and <_broken>"

        # When validation is disabled, we should skip it
        # (simulating what the node does)
        if policy.validate_output:
            issues = validate_placeholders(response, ctx.placeholder_map)
            assert len(issues) > 0
        else:
            # Skip validation
            issues = []

        # Without validation, we proceed to unmask
        result = unmask_text(response, ctx)
        # <_broken> remains as-is since it's not in mapping
        assert "<_broken>" in result

    def test_pipeline_environment_configuration(self) -> None:
        """Test that all settings can be configured via environment."""
        # Set custom configuration
        os.environ["OPENSRE_MASK_MAX_PLACEHOLDERS"] = "50"
        os.environ["OPENSRE_MASK_VALIDATE_OUTPUT"] = "false"
        os.environ["OPENSRE_MASK_PANIC_THRESHOLD"] = "5"

        try:
            policy = MaskingPolicy.from_env()

            assert policy.max_placeholders == 50
            assert policy.validate_output is False
            assert policy.panic_threshold == 5

            ctx = MaskingContext.create(policy)

            # Verify settings applied
            assert ctx.placeholder_map.max_placeholders == 50

        finally:
            # Cleanup
            del os.environ["OPENSRE_MASK_MAX_PLACEHOLDERS"]
            del os.environ["OPENSRE_MASK_VALIDATE_OUTPUT"]
            del os.environ["OPENSRE_MASK_PANIC_THRESHOLD"]


class TestPipelineEdgeCases:
    """Edge cases for the masking pipeline."""

    def test_empty_and_null_values(self) -> None:
        """Test handling of empty and None values."""
        ctx = MaskingContext.create()

        data = {
            "empty_string": "",
            "none_value": None,
            "valid": "Error in cluster-prod-01",
        }

        result = mask_dict(data, ctx)

        # Empty and None should pass through
        assert result["empty_string"] == ""
        assert result["none_value"] is None
        # Valid should be masked
        assert "cluster-prod-01" not in result["valid"]

    def test_unicode_and_special_characters(self) -> None:
        """Test handling of unicode in identifiers."""
        ctx = MaskingContext.create()

        # Unicode hostname (IDN)
        text = "Error connecting to münchen.example.com: timeout"
        result = ctx.mask_text(text)

        # Should still mask or pass through safely
        assert "münchen.example.com" not in result or result != text

    def test_very_large_text_performance(self) -> None:
        """Test performance with large text content."""
        ctx = MaskingContext.create()

        # Generate large text with many identifiers
        large_text = "\n".join(
            f"Log line {i}: Error in cluster-prod-{i % 100} at 192.168.{i // 256}.{i % 256}"
            for i in range(1000)
        )

        # Should complete without timeout
        result = ctx.mask_text(large_text)

        # Verify at least some masking occurred
        assert "<IP_" in result or "<CLUSTER_" in result
        # Verify no exceptions raised
        assert isinstance(result, str)
