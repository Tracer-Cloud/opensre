"""Integration tests for the masking module."""

import os
from collections.abc import Generator

import pytest

from app.utils.masking import (
    CompiledPolicy,
    DetectedIdentifier,
    IdentifierType,
    MaskingContext,
    MaskingPolicy,
    PlaceholderMap,
    find_identifiers,
    mask_dict,
    mask_list,
    mask_text,
    reset_global_context,
    unmask_dict,
    unmask_list,
    unmask_text,
)


class TestEndToEnd:
    """End-to-end tests for complete masking workflows."""

    @pytest.fixture(autouse=True)
    def reset_env(self) -> Generator[None, None, None]:
        """Reset environment and global context."""
        reset_global_context()
        env_vars = [
            "OPENSRE_MASK_HOSTNAMES",
            "OPENSRE_MASK_ACCOUNT_IDS",
            "OPENSRE_MASK_CLUSTER_NAMES",
            "OPENSRE_MASK_SERVICE_NAMES",
            "OPENSRE_MASK_IP_ADDRESSES",
            "OPENSRE_MASK_EMAILS",
            "OPENSRE_MASK_CUSTOM_PATTERNS",
        ]
        old_values: dict[str, str | None] = {}
        for var in env_vars:
            old_values[var] = os.environ.get(var)
            if var in os.environ:
                del os.environ[var]

        yield

        for var in env_vars:
            if old_values[var] is not None:
                os.environ[var] = old_values[var]  # type: ignore
            elif var in os.environ:
                del os.environ[var]

    def test_full_workflow_basic(self) -> None:
        """Test complete masking workflow from detection to restoration."""
        # Create policy from env (all defaults)
        policy = MaskingPolicy.from_env()
        ctx = MaskingContext.create(policy)

        # Original sensitive data
        alert = (
            "Critical error in eks-production-cluster "
            "at IP 10.0.1.100. Contact admin@company.com "
            "AWS account 987654321098 affected."
        )

        # Mask
        masked = ctx.mask_text(alert)

        # Verify all sensitive data masked
        assert "eks-production-cluster" not in masked
        assert "10.0.1.100" not in masked
        assert "admin@company.com" not in masked
        assert "987654321098" not in masked

        # Verify placeholders present
        assert "<CLUSTER_0>" in masked or "<CLUSTER_1>" in masked
        assert "<IP_0>" in masked
        assert "<EMAIL_0>" in masked
        assert "<ACCOUNT_0>" in masked

        # Unmask
        unmasked = ctx.unmask_text(masked)
        assert unmasked == alert

    def test_full_workflow_with_dict(self) -> None:
        """Test complete workflow with dictionary data."""
        policy = MaskingPolicy.from_env()
        ctx = MaskingContext.create(policy)

        alert_data = {
            "title": "Service degradation",
            "cluster": "cluster-api-prod",
            "affected_service": "service-payment-gateway",
            "logs": ["192.168.10.5 connection refused", "db-master.internal.com timeout"],
            "metadata": {"account_id": "555555555555", "reported_by": "oncall@company.com"},
        }

        # Mask
        masked = mask_dict(alert_data, ctx)

        # Verify masking
        assert "cluster-api-prod" not in str(masked)
        assert "service-payment-gateway" not in str(masked)
        assert "192.168.10.5" not in str(masked)
        assert "555555555555" not in str(masked)

        # Unmask
        unmasked = unmask_dict(masked, ctx)
        assert unmasked == alert_data

    def test_configuration_via_environment(self) -> None:
        """Test that policy can be configured via environment variables."""
        # Configure via env
        os.environ["OPENSRE_MASK_CLUSTER_NAMES"] = "false"
        os.environ["OPENSRE_MASK_HOSTNAMES"] = "true"
        os.environ["OPENSRE_MASK_ACCOUNT_IDS"] = "false"

        policy = MaskingPolicy.from_env()

        assert policy.mask_cluster_names is False
        assert policy.mask_hostnames is True
        assert policy.mask_account_ids is False

        ctx = MaskingContext.create(policy)

        text = "cluster-prod-01 at 192.168.1.1 with account 123456789012"
        masked = ctx.mask_text(text)

        # Cluster and account should not be masked
        assert "cluster-prod-01" in masked
        assert "123456789012" in masked
        # Hostname/IP should be masked
        assert "192.168.1.1" not in masked

    def test_custom_patterns_via_environment(self) -> None:
        """Test custom patterns configured via environment."""
        os.environ["OPENSRE_MASK_CUSTOM_PATTERNS"] = r"ERROR-\d{4},WARN-\d{3}"

        policy = MaskingPolicy.from_env()
        assert len(policy.custom_patterns) == 2

        ctx = MaskingContext.create(policy)

        text = "Errors: ERROR-1234, WARN-567 in cluster-prod-01"
        masked = ctx.mask_text(text)

        # Custom patterns should be masked
        assert "ERROR-1234" not in masked
        assert "WARN-567" not in masked
        # Regular identifiers still masked
        assert "cluster-prod-01" not in masked

    def test_multiple_investigation_isolation(self) -> None:
        """Test that different investigations have isolated contexts."""
        # Investigation 1
        ctx1 = MaskingContext.create()
        text1 = "Error in cluster-alpha"
        masked1 = ctx1.mask_text(text1)

        # Investigation 2
        ctx2 = MaskingContext.create()
        text2 = "Error in cluster-beta"
        masked2 = ctx2.mask_text(text2)

        # Both should start with index 0
        assert "<CLUSTER_0>" in masked1
        assert "<CLUSTER_0>" in masked2

        # But they map to different values
        assert ctx1.unmask_text("<CLUSTER_0>") == "cluster-alpha"
        assert ctx2.unmask_text("<CLUSTER_0>") == "cluster-beta"

    def test_repeated_calls_consistency(self) -> None:
        """Test that repeated calls are consistent within context."""
        ctx = MaskingContext.create()

        # First call
        r1 = ctx.mask_text("Check cluster-prod-01")
        # Second call with same value
        r2 = ctx.mask_text("Restart cluster-prod-01")

        # Same value should produce same placeholder
        assert r1 == "Check <CLUSTER_0>"
        assert r2 == "Restart <CLUSTER_0>"

        # Unmask both
        assert ctx.unmask_text(r1) == "Check cluster-prod-01"
        assert ctx.unmask_text(r2) == "Restart cluster-prod-01"


class TestPublicAPI:
    """Tests verifying the public API surface."""

    def test_all_exports_available(self) -> None:
        """Test that all expected exports are available."""
        # Core utilities
        assert callable(mask_text)
        assert callable(unmask_text)
        assert callable(mask_dict)
        assert callable(unmask_dict)
        assert callable(mask_list)
        assert callable(unmask_list)
        assert callable(reset_global_context)

        # Classes
        assert MaskingContext is not None
        assert MaskingPolicy is not None
        assert CompiledPolicy is not None
        assert PlaceholderMap is not None
        assert DetectedIdentifier is not None

        # Enums and functions
        assert IdentifierType is not None
        assert callable(find_identifiers)

    def test_documentation_example(self) -> None:
        """Test the example from module docstring."""
        ctx = MaskingContext.create()

        masked = ctx.mask_text("Error in prod-cluster-01: connection to api.example.com failed")
        assert "<CLUSTER_0>" in masked
        assert "<HOSTNAME_0>" in masked

        unmasked = ctx.unmask_text("Check logs for <CLUSTER_0>")
        assert unmasked == "Check logs for prod-cluster-01"
