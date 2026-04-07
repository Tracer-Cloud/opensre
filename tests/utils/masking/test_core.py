"""Tests for core masking functionality."""

import os
from collections.abc import Generator

import pytest

from app.utils.masking.core import (
    MaskingContext,
    mask_dict,
    mask_list,
    mask_text,
    reset_global_context,
    unmask_dict,
    unmask_list,
    unmask_text,
)
from app.utils.masking.policies import MaskingPolicy


class TestMaskingContext:
    """Tests for MaskingContext class."""

    def test_create_with_default_policy(self) -> None:
        """Test creating context with default policy."""
        ctx = MaskingContext.create()
        assert ctx.policy.policy.mask_hostnames is True

    def test_create_with_custom_policy(self) -> None:
        """Test creating context with custom policy."""
        policy = MaskingPolicy(mask_hostnames=False)
        ctx = MaskingContext.create(policy)
        assert ctx.policy.policy.mask_hostnames is False

    def test_mask_text_no_identifiers(self) -> None:
        """Test masking text with no sensitive identifiers."""
        ctx = MaskingContext.create()
        text = "No sensitive data here"
        result = ctx.mask_text(text)
        assert result == text

    def test_mask_text_with_hostname(self) -> None:
        """Test masking hostname in text."""
        policy = MaskingPolicy(mask_hostnames=True, mask_cluster_names=False)
        ctx = MaskingContext.create(policy)
        text = "Error connecting to api.example.com: timeout"
        result = ctx.mask_text(text)
        assert "api.example.com" not in result
        assert "<HOSTNAME_0>" in result

    def test_mask_text_with_cluster_name(self) -> None:
        """Test masking cluster name in text."""
        policy = MaskingPolicy(mask_hostnames=False, mask_cluster_names=True)
        ctx = MaskingContext.create(policy)
        text = "Error in cluster-prod-01: failed"
        result = ctx.mask_text(text)
        assert "cluster-prod-01" not in result
        assert "<CLUSTER_0>" in result

    def test_mask_text_with_account_id(self) -> None:
        """Test masking AWS account ID in text."""
        policy = MaskingPolicy(mask_account_ids=True)
        ctx = MaskingContext.create(policy)
        text = "ARN: arn:aws:iam::123456789012:role/admin"
        result = ctx.mask_text(text)
        assert "123456789012" not in result
        assert "<ACCOUNT_0>" in result

    def test_mask_text_with_ip_address(self) -> None:
        """Test masking IP address in text."""
        policy = MaskingPolicy(mask_ip_addresses=True)
        ctx = MaskingContext.create(policy)
        text = "Connection to 192.168.1.1 refused"
        result = ctx.mask_text(text)
        assert "192.168.1.1" not in result
        assert "<IP_0>" in result

    def test_mask_text_with_email(self) -> None:
        """Test masking email in text."""
        policy = MaskingPolicy(mask_emails=True)
        ctx = MaskingContext.create(policy)
        text = "Contact admin@example.com for help"
        result = ctx.mask_text(text)
        assert "admin@example.com" not in result
        assert "<EMAIL_0>" in result

    def test_mask_text_multiple_same_value(self) -> None:
        """Test that repeated values get same placeholder."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)
        text = "Check cluster-prod-01 and restart cluster-prod-01"
        result = ctx.mask_text(text)

        # Should use same placeholder for both occurrences
        assert result.count("<CLUSTER_0>") == 2
        assert "cluster-prod-01" not in result

    def test_mask_text_multiple_different_values(self) -> None:
        """Test that different values get different placeholders."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)
        text = "Check cluster-prod-01 and cluster-prod-02"
        result = ctx.mask_text(text)

        # Should use different placeholders
        assert "<CLUSTER_0>" in result
        assert "<CLUSTER_1>" in result

    def test_mask_text_disabled_policy(self) -> None:
        """Test that disabled policy returns text unchanged."""
        policy = MaskingPolicy(
            mask_hostnames=False,
            mask_cluster_names=False,
            mask_account_ids=False,
            mask_service_names=False,
            mask_ip_addresses=False,
            mask_emails=False,
        )
        ctx = MaskingContext.create(policy)
        text = "Error in cluster-prod-01 at api.example.com"
        result = ctx.mask_text(text)
        assert result == text

    def test_unmask_text(self) -> None:
        """Test unmasking text with placeholders."""
        policy = MaskingPolicy(mask_cluster_names=True, mask_hostnames=True)
        ctx = MaskingContext.create(policy)

        # First mask
        original = "Error in cluster-prod-01: connection to api.example.com failed"
        masked = ctx.mask_text(original)

        # Then unmask
        unmasked = ctx.unmask_text(masked)
        assert unmasked == original

    def test_unmask_text_partial(self) -> None:
        """Test unmasking text with mixed masked and unmasked content."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        # Mask first
        ctx.mask_text("Error in cluster-prod-01")

        # Unmask a response
        response = "Check logs for <CLUSTER_0> and verify"
        unmasked = ctx.unmask_text(response)
        assert unmasked == "Check logs for cluster-prod-01 and verify"

    def test_get_stats(self) -> None:
        """Test getting statistics about masked identifiers."""
        policy = MaskingPolicy(mask_cluster_names=True, mask_hostnames=True)
        ctx = MaskingContext.create(policy)

        ctx.mask_text("Error in cluster-prod-01 at api.example.com")
        ctx.mask_text("Also check cluster-prod-02")

        stats = ctx.get_stats()
        # Stats now use full IdentifierType enum names (e.g., "CLUSTER_NAME", "HOSTNAME")
        assert stats.get("CLUSTER_NAME", 0) == 2
        assert stats.get("HOSTNAME", 0) == 1


class TestMaskingConvenienceFunctions:
    """Tests for convenience functions using global context."""

    @pytest.fixture(autouse=True)
    def reset_global(self) -> Generator[None, None, None]:
        """Reset global context before each test."""
        reset_global_context()
        # Clear env to get predictable defaults
        env_vars = [
            "OPENSRE_MASK_HOSTNAMES",
            "OPENSRE_MASK_ACCOUNT_IDS",
            "OPENSRE_MASK_CLUSTER_NAMES",
            "OPENSRE_MASK_SERVICE_NAMES",
            "OPENSRE_MASK_IP_ADDRESSES",
            "OPENSRE_MASK_EMAILS",
        ]
        old_values: dict[str, str | None] = {}
        for var in env_vars:
            old_values[var] = os.environ.get(var)
            if var in os.environ:
                del os.environ[var]

        yield

        # Restore
        for var in env_vars:
            if old_values[var] is not None:
                os.environ[var] = old_values[var]  # type: ignore
            elif var in os.environ:
                del os.environ[var]

    def test_mask_text_global(self) -> None:
        """Test global mask_text function."""
        result = mask_text("Error in cluster-prod-01")
        assert "cluster-prod-01" not in result
        assert "<CLUSTER_0>" in result

    def test_unmask_text_global(self) -> None:
        """Test global unmask_text function."""
        # First mask to populate global context
        mask_text("Error in cluster-prod-01")

        # Then unmask
        result = unmask_text("Check <CLUSTER_0>")
        assert result == "Check cluster-prod-01"


class TestMaskDict:
    """Tests for mask_dict function."""

    def test_mask_dict_flat(self) -> None:
        """Test masking flat dictionary."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        data = {"error": "Failure in cluster-prod-01", "status": "ok"}
        result = mask_dict(data, ctx)

        assert "cluster-prod-01" not in result["error"]
        assert "<CLUSTER_0>" in result["error"]
        assert result["status"] == "ok"

    def test_mask_dict_nested(self) -> None:
        """Test masking nested dictionary."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        data = {"level1": {"level2": {"message": "Error in cluster-prod-01"}}}
        result = mask_dict(data, ctx)

        assert "cluster-prod-01" not in result["level1"]["level2"]["message"]
        assert "<CLUSTER_0>" in result["level1"]["level2"]["message"]

    def test_mask_dict_with_list(self) -> None:
        """Test masking dictionary containing lists."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        data = {"clusters": ["cluster-prod-01", "cluster-prod-02"], "message": "Check clusters"}
        result = mask_dict(data, ctx)

        assert result["clusters"] == ["<CLUSTER_0>", "<CLUSTER_1>"]


class TestMaskList:
    """Tests for mask_list function."""

    def test_mask_list_strings(self) -> None:
        """Test masking list of strings."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        data = ["cluster-prod-01", "cluster-prod-02"]
        result = mask_list(data, ctx)

        assert result == ["<CLUSTER_0>", "<CLUSTER_1>"]

    def test_mask_list_nested_dicts(self) -> None:
        """Test masking list of dictionaries."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        data = [{"name": "cluster-prod-01"}, {"name": "cluster-prod-02"}]
        result = mask_list(data, ctx)

        assert result[0]["name"] == "<CLUSTER_0>"
        assert result[1]["name"] == "<CLUSTER_1>"


class TestUnmaskDict:
    """Tests for unmask_dict function."""

    def test_unmask_dict_flat(self) -> None:
        """Test unmasking flat dictionary."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        # Populate mappings
        ctx.mask_text("Error in cluster-prod-01")

        data = {"error": "Check <CLUSTER_0> logs", "status": "ok"}
        result = unmask_dict(data, ctx)

        assert result["error"] == "Check cluster-prod-01 logs"
        assert result["status"] == "ok"


class TestUnmaskList:
    """Tests for unmask_list function."""

    def test_unmask_list_strings(self) -> None:
        """Test unmasking list of strings."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        # Populate mappings by masking each value individually
        # This ensures we know which placeholder maps to which value
        ctx.mask_text("Error in cluster-prod-01")
        ctx.mask_text("Error in cluster-prod-02")

        # Verify mappings before unmasking
        assert ctx.placeholder_map.get_original_value("<CLUSTER_0>") == "cluster-prod-01"
        assert ctx.placeholder_map.get_original_value("<CLUSTER_1>") == "cluster-prod-02"

        data = ["Check <CLUSTER_0>", "Restart <CLUSTER_1>"]
        result = unmask_list(data, ctx)

        assert result == ["Check cluster-prod-01", "Restart cluster-prod-02"]


class TestRoundTrip:
    """Tests for complete round-trip masking and unmasking."""

    def test_round_trip_simple(self) -> None:
        """Test simple round-trip masking and unmasking."""
        policy = MaskingPolicy(
            mask_cluster_names=True,
            mask_hostnames=False,
        )
        ctx = MaskingContext.create(policy)

        original = "Error in cluster-prod-01"
        masked = ctx.mask_text(original)
        unmasked = ctx.unmask_text(masked)

        assert unmasked == original

    def test_round_trip_multiple_identifiers(self) -> None:
        """Test round-trip with multiple types of identifiers."""
        policy = MaskingPolicy(
            mask_cluster_names=True,
            mask_hostnames=True,
            mask_account_ids=True,
            mask_ip_addresses=True,
        )
        ctx = MaskingContext.create(policy)

        original = (
            "Error in cluster-prod-01: "
            "Connection from 192.168.1.1 to api.example.com "
            "using account 123456789012 failed"
        )
        masked = ctx.mask_text(original)
        unmasked = ctx.unmask_text(masked)

        assert unmasked == original

    def test_round_trip_llm_response_scenario(self) -> None:
        """Test round-trip simulating real LLM interaction."""
        policy = MaskingPolicy(
            mask_cluster_names=True,
            mask_hostnames=True,
        )
        ctx = MaskingContext.create(policy)

        # Original alert data
        alert_data = {
            "title": "Error in cluster-prod-01",
            "description": "Connection to api.example.com refused",
            "source": "monitoring",
        }

        # Mask before sending to LLM (establishes placeholder mappings)
        _ = mask_dict(alert_data, ctx)

        # Simulate LLM response with placeholders
        llm_response = {
            "analysis": "The error in <CLUSTER_0> indicates connectivity issues",
            "recommendation": "Check network path to <HOSTNAME_0>",
            "confidence": 0.85,
        }

        # Unmask the response
        unmasked_response = unmask_dict(llm_response, ctx)

        assert "cluster-prod-01" in unmasked_response["analysis"]
        assert "api.example.com" in unmasked_response["recommendation"]
        assert "<CLUSTER_0>" not in unmasked_response["analysis"]
        assert "<HOSTNAME_0>" not in unmasked_response["recommendation"]

    def test_round_trip_repeated_identifiers(self) -> None:
        """Test that repeated identifiers maintain stable mapping."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        # Multiple occurrences of same identifier
        original = (
            "cluster-prod-01 is down. Restart cluster-prod-01. Verify cluster-prod-01 status."
        )
        masked = ctx.mask_text(original)

        # Should all use same placeholder
        assert masked.count("<CLUSTER_0>") == 3
        assert "cluster-prod-01" not in masked

        # Unmask should restore all
        unmasked = ctx.unmask_text(masked)
        assert unmasked == original
        assert unmasked.count("cluster-prod-01") == 3

    def test_round_trip_across_multiple_texts(self) -> None:
        """Test round-trip across multiple mask/unmask operations."""
        policy = MaskingPolicy(mask_cluster_names=True)
        ctx = MaskingContext.create(policy)

        # First text establishes mapping
        text1 = "Error in cluster-prod-01"
        masked1 = ctx.mask_text(text1)

        # Second text should use same placeholder for same value
        text2 = "Also check cluster-prod-01 logs"
        masked2 = ctx.mask_text(text2)

        # Both should use <CLUSTER_0>
        assert "<CLUSTER_0>" in masked1
        assert "<CLUSTER_0>" in masked2

        # Unmask both
        unmasked1 = ctx.unmask_text(masked1)
        unmasked2 = ctx.unmask_text(masked2)

        assert unmasked1 == text1
        assert unmasked2 == text2

    def test_round_trip_complex_nested_structure(self) -> None:
        """Test round-trip with complex nested data structure."""
        policy = MaskingPolicy(
            mask_cluster_names=True,
            mask_hostnames=True,
            mask_account_ids=True,
        )
        ctx = MaskingContext.create(policy)

        # Complex nested structure
        original_data = {
            "incident": {
                "cluster": "cluster-prod-01",
                "affected_hosts": ["api.example.com", "db.example.com"],
                "aws_account": "123456789012",
            },
            "logs": [
                {"timestamp": "2024-01-01", "message": "Error in cluster-prod-01"},
                {"timestamp": "2024-01-02", "message": "Retry from api.example.com"},
            ],
        }

        # Mask entire structure
        masked_data = mask_dict(original_data, ctx)

        # Verify masking occurred
        assert "cluster-prod-01" not in str(masked_data)
        assert "<CLUSTER_0>" in str(masked_data)
        assert "<HOSTNAME_0>" in str(masked_data)
        assert "<ACCOUNT_0>" in str(masked_data)

        # Unmask
        unmasked_data = unmask_dict(masked_data, ctx)

        # Verify restoration
        assert unmasked_data == original_data


class TestDetectRemainingPlaceholders:
    """Tests for detect_remaining_placeholders function."""

    def test_detects_valid_placeholders(self) -> None:
        """Should detect valid-format placeholders in text."""
        from app.utils.masking.core import detect_remaining_placeholders

        text = "Check <CLUSTER_0> and <HOSTNAME_5> for issues"
        remaining = detect_remaining_placeholders(text)

        assert "<CLUSTER_0>" in remaining
        assert "<HOSTNAME_5>" in remaining
        assert len(remaining) == 2

    def test_returns_empty_when_none(self) -> None:
        """Should return empty list when no placeholders present."""
        from app.utils.masking.core import detect_remaining_placeholders

        text = "Normal text without placeholders"
        remaining = detect_remaining_placeholders(text)

        assert remaining == []

    def test_detects_multiple_of_same_type(self) -> None:
        """Should detect multiple placeholders of the same type."""
        from app.utils.masking.core import detect_remaining_placeholders

        text = "Issues with <CLUSTER_0>, <CLUSTER_1>, and <CLUSTER_2>"
        remaining = detect_remaining_placeholders(text)

        assert len(remaining) == 3
        assert remaining.count("<CLUSTER_0>") == 1
        assert remaining.count("<CLUSTER_1>") == 1
        assert remaining.count("<CLUSTER_2>") == 1

    def test_detects_all_types(self) -> None:
        """Should detect all valid placeholder types."""
        from app.utils.masking.core import detect_remaining_placeholders

        text = (
            "<HOSTNAME_0> <ACCOUNT_1> <CLUSTER_2> "
            "<SERVICE_3> <IP_4> <EMAIL_5> <CUSTOM_6> <MASKED_7>"
        )
        remaining = detect_remaining_placeholders(text)

        assert len(remaining) == 8
        assert "<HOSTNAME_0>" in remaining
        assert "<ACCOUNT_1>" in remaining
        assert "<CLUSTER_2>" in remaining
        assert "<SERVICE_3>" in remaining
        assert "<IP_4>" in remaining
        assert "<EMAIL_5>" in remaining
        assert "<CUSTOM_6>" in remaining
        assert "<MASKED_7>" in remaining

    def test_ignores_invalid_formats(self) -> None:
        """Should ignore placeholders with invalid formats."""
        from app.utils.masking.core import detect_remaining_placeholders

        # Invalid formats that should NOT be detected
        text = "Invalid: <_0>, <UNKNOWN_0>, <HOSTNAME>, <HOSTNAME_abc>, <<HOSTNAME_0>>"
        remaining = detect_remaining_placeholders(text)

        # <_0> - missing type prefix (not a valid pattern)
        # <UNKNOWN_0> - not in valid type list
        # <HOSTNAME> - missing number
        # <HOSTNAME_abc> - non-numeric index
        # <<HOSTNAME_0>> - double brackets (outer >> makes it invalid)

        # Only valid format <TYPE_N> should be detected
        # None of the above are valid formats per the regex
        assert "<_0>" not in remaining
        assert "<UNKNOWN_0>" not in remaining
        assert "<HOSTNAME>" not in remaining
        # <<HOSTNAME_0>> has double angle brackets so the regex won't match it
