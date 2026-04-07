"""Tests for masking policies."""

import os
from collections.abc import Generator

import pytest

from app.utils.masking.policies import CompiledPolicy, MaskingPolicy


class TestMaskingPolicy:
    """Tests for MaskingPolicy configuration."""

    def test_default_values(self) -> None:
        """Test default masking policy values."""
        policy = MaskingPolicy()
        assert policy.mask_hostnames is True
        assert policy.mask_account_ids is True
        assert policy.mask_cluster_names is True
        assert policy.mask_service_names is True
        assert policy.mask_ip_addresses is True
        assert policy.mask_emails is True
        assert policy.custom_patterns == []

    def test_is_any_enabled_all_enabled(self) -> None:
        """Test is_any_enabled when all options are enabled."""
        policy = MaskingPolicy()
        assert policy.is_any_enabled() is True

    def test_is_any_enabled_all_disabled(self) -> None:
        """Test is_any_enabled when all options are disabled."""
        policy = MaskingPolicy(
            mask_hostnames=False,
            mask_account_ids=False,
            mask_cluster_names=False,
            mask_service_names=False,
            mask_ip_addresses=False,
            mask_emails=False,
        )
        assert policy.is_any_enabled() is False

    def test_is_any_enabled_with_custom_patterns(self) -> None:
        """Test is_any_enabled with only custom patterns."""
        policy = MaskingPolicy(
            mask_hostnames=False,
            mask_account_ids=False,
            mask_cluster_names=False,
            mask_service_names=False,
            mask_ip_addresses=False,
            mask_emails=False,
            custom_patterns=[r"\d+"],
        )
        assert policy.is_any_enabled() is True


class TestMaskingPolicyFromEnv:
    """Tests for MaskingPolicy.from_env() method."""

    @pytest.fixture(autouse=True)
    def clear_env(self) -> Generator[None, None, None]:
        """Clear relevant environment variables before each test."""
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

        # Restore old values
        for var in env_vars:
            if old_values[var] is not None:
                os.environ[var] = old_values[var]  # type: ignore
            elif var in os.environ:
                del os.environ[var]

    def test_from_env_defaults(self) -> None:
        """Test that from_env uses correct defaults."""
        policy = MaskingPolicy.from_env()
        assert policy.mask_hostnames is True
        assert policy.mask_account_ids is True
        assert policy.mask_cluster_names is True
        assert policy.mask_service_names is True
        assert policy.mask_ip_addresses is True
        assert policy.mask_emails is True

    def test_from_env_disable_hostnames(self) -> None:
        """Test disabling hostnames via env."""
        os.environ["OPENSRE_MASK_HOSTNAMES"] = "false"
        policy = MaskingPolicy.from_env()
        assert policy.mask_hostnames is False
        assert policy.mask_account_ids is True

    def test_from_env_disable_all(self) -> None:
        """Test disabling all via env."""
        os.environ["OPENSRE_MASK_HOSTNAMES"] = "0"
        os.environ["OPENSRE_MASK_ACCOUNT_IDS"] = "no"
        os.environ["OPENSRE_MASK_CLUSTER_NAMES"] = "off"
        policy = MaskingPolicy.from_env()
        assert policy.mask_hostnames is False
        assert policy.mask_account_ids is False
        assert policy.mask_cluster_names is False

    def test_from_env_enable_variations(self) -> None:
        """Test various ways to enable via env."""
        os.environ["OPENSRE_MASK_HOSTNAMES"] = "1"
        os.environ["OPENSRE_MASK_ACCOUNT_IDS"] = "yes"
        os.environ["OPENSRE_MASK_CLUSTER_NAMES"] = "on"
        policy = MaskingPolicy.from_env()
        assert policy.mask_hostnames is True
        assert policy.mask_account_ids is True
        assert policy.mask_cluster_names is True

    def test_from_env_custom_patterns(self) -> None:
        """Test custom patterns from env."""
        os.environ["OPENSRE_MASK_CUSTOM_PATTERNS"] = r"\d{4},[A-Z]+,test-\w+"
        policy = MaskingPolicy.from_env()
        assert policy.custom_patterns == [r"\d{4}", r"[A-Z]+", r"test-\w+"]

    def test_from_env_custom_patterns_whitespace(self) -> None:
        """Test custom patterns with whitespace handling."""
        os.environ["OPENSRE_MASK_CUSTOM_PATTERNS"] = r"  \d+  ,  \w+  "
        policy = MaskingPolicy.from_env()
        assert policy.custom_patterns == [r"\d+", r"\w+"]

    def test_from_env_empty_custom_patterns(self) -> None:
        """Test empty custom patterns from env."""
        os.environ["OPENSRE_MASK_CUSTOM_PATTERNS"] = ""
        policy = MaskingPolicy.from_env()
        assert policy.custom_patterns == []


class TestCompiledPolicy:
    """Tests for CompiledPolicy."""

    def test_compiled_patterns_all_enabled(self) -> None:
        """Test that all patterns are compiled when enabled."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)

        assert compiled.hostname_pattern is not None
        assert compiled.account_id_pattern is not None
        assert compiled.cluster_name_pattern is not None
        assert compiled.service_name_pattern is not None
        assert compiled.ip_address_pattern is not None
        assert compiled.email_pattern is not None

    def test_compiled_patterns_disabled(self) -> None:
        """Test that patterns are None when disabled."""
        policy = MaskingPolicy(
            mask_hostnames=False,
            mask_account_ids=False,
        )
        compiled = CompiledPolicy.from_policy(policy)

        assert compiled.hostname_pattern is None
        assert compiled.account_id_pattern is None
        assert compiled.cluster_name_pattern is not None  # Still enabled

    def test_hostname_pattern_matches(self) -> None:
        """Test that hostname pattern matches hostnames."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.hostname_pattern is not None

        # Should match
        assert compiled.hostname_pattern.search("example.com")
        assert compiled.hostname_pattern.search("sub.example.co.uk")
        assert compiled.hostname_pattern.search("api-service.example.com")

    def test_account_id_pattern_matches(self) -> None:
        """Test that account ID pattern matches 12-digit AWS account IDs."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.account_id_pattern is not None

        # Should match 12-digit numbers
        assert compiled.account_id_pattern.search("123456789012")
        match = compiled.account_id_pattern.search("arn:aws:iam::123456789012:role/my-role")
        assert match is not None
        assert match.group(0) == "123456789012"

        # Should not match other lengths
        assert not compiled.account_id_pattern.search("12345678901")  # 11 digits
        assert not compiled.account_id_pattern.search("1234567890123")  # 13 digits

    def test_cluster_name_pattern_matches(self) -> None:
        """Test that cluster name pattern matches cluster identifiers."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.cluster_name_pattern is not None

        # Should match cluster-like names
        assert compiled.cluster_name_pattern.search("cluster-prod-01")
        assert compiled.cluster_name_pattern.search("eks-us-east-1")
        assert compiled.cluster_name_pattern.search("k8s-main-cluster")
        assert compiled.cluster_name_pattern.search("kubernetes-default")

    def test_service_name_pattern_matches(self) -> None:
        """Test that service name pattern matches service identifiers."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.service_name_pattern is not None

        # Should match service-like names
        assert compiled.service_name_pattern.search("service-api-gateway")
        assert compiled.service_name_pattern.search("svc-auth-service")
        assert compiled.service_name_pattern.search("app-frontend")
        assert compiled.service_name_pattern.search("api-payment-processor")

    def test_ip_address_pattern_matches(self) -> None:
        """Test that IP pattern matches IP addresses."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.ip_address_pattern is not None

        # Should match IPv4 addresses
        assert compiled.ip_address_pattern.search("192.168.1.1")
        assert compiled.ip_address_pattern.search("10.0.0.0")
        assert compiled.ip_address_pattern.search("255.255.255.255")

    def test_email_pattern_matches(self) -> None:
        """Test that email pattern matches email addresses."""
        policy = MaskingPolicy()
        compiled = CompiledPolicy.from_policy(policy)
        assert compiled.email_pattern is not None

        # Should match email addresses
        assert compiled.email_pattern.search("user@example.com")
        assert compiled.email_pattern.search("admin@company.co.uk")

    def test_custom_patterns_compiled(self) -> None:
        """Test that custom patterns are compiled correctly."""
        policy = MaskingPolicy(custom_patterns=[r"\d{4}", r"[A-Z]{3}"])
        compiled = CompiledPolicy.from_policy(policy)

        assert len(compiled.custom_patterns) == 2
        assert compiled.custom_patterns[0].pattern == r"\d{4}"
        assert compiled.custom_patterns[1].pattern == r"[A-Z]{3}"

    def test_custom_patterns_invalid_regex(self) -> None:
        """Test that invalid regex patterns raise ValueError."""
        policy = MaskingPolicy(custom_patterns=[r"\d{4}", r"[invalid(", r"[A-Z]+"])

        # Should raise ValueError with clear message about invalid pattern
        with pytest.raises(ValueError) as exc_info:
            CompiledPolicy.from_policy(policy)

        assert "Invalid custom regex pattern" in str(exc_info.value)
        assert "[invalid(" in str(exc_info.value)
