"""Masking policy models for sensitive infrastructure identifiers.

Policies are configurable via environment variables without code changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from re import Pattern

from app.strict_config import StrictConfigModel


class MaskingPolicy(StrictConfigModel):
    """Configuration for what types of identifiers to mask.

    All settings can be configured via environment variables:
    - OPENSRE_MASK_HOSTNAMES: mask hostnames (default: true)
    - OPENSRE_MASK_ACCOUNT_IDS: mask AWS/GCP/Azure account IDs (default: true)
    - OPENSRE_MASK_CLUSTER_NAMES: mask cluster names (default: true)
    - OPENSRE_MASK_SERVICE_NAMES: mask service names (default: true)
    - OPENSRE_MASK_IP_ADDRESSES: mask IP addresses (default: true)
    - OPENSRE_MASK_EMAILS: mask email addresses (default: true)
    - OPENSRE_MASK_CUSTOM_PATTERNS: comma-separated list of regex patterns to mask
    """

    mask_hostnames: bool = True
    mask_account_ids: bool = True
    mask_cluster_names: bool = True
    mask_service_names: bool = True
    mask_ip_addresses: bool = True
    mask_emails: bool = True
    custom_patterns: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> MaskingPolicy:
        """Build masking policy from environment variables."""

        def _env_bool(name: str, default: bool) -> bool:
            value = os.getenv(name, "").strip().lower()
            if value in ("1", "true", "yes", "on"):
                return True
            if value in ("0", "false", "no", "off"):
                return False
            return default

        custom_patterns = []
        custom_env = os.getenv("OPENSRE_MASK_CUSTOM_PATTERNS", "").strip()
        if custom_env:
            custom_patterns = [p.strip() for p in custom_env.split(",") if p.strip()]

        return cls(
            mask_hostnames=_env_bool("OPENSRE_MASK_HOSTNAMES", True),
            mask_account_ids=_env_bool("OPENSRE_MASK_ACCOUNT_IDS", True),
            mask_cluster_names=_env_bool("OPENSRE_MASK_CLUSTER_NAMES", True),
            mask_service_names=_env_bool("OPENSRE_MASK_SERVICE_NAMES", True),
            mask_ip_addresses=_env_bool("OPENSRE_MASK_IP_ADDRESSES", True),
            mask_emails=_env_bool("OPENSRE_MASK_EMAILS", True),
            custom_patterns=custom_patterns,
        )

    def is_any_enabled(self) -> bool:
        """Check if any masking is enabled."""
        return (
            self.mask_hostnames
            or self.mask_account_ids
            or self.mask_cluster_names
            or self.mask_service_names
            or self.mask_ip_addresses
            or self.mask_emails
            or bool(self.custom_patterns)
        )


@dataclass(frozen=True)
class CompiledPolicy:
    """Compiled version of masking policy with regex patterns."""

    policy: MaskingPolicy
    hostname_pattern: Pattern[str] | None = None
    account_id_pattern: Pattern[str] | None = None
    cluster_name_pattern: Pattern[str] | None = None
    service_name_pattern: Pattern[str] | None = None
    ip_address_pattern: Pattern[str] | None = None
    email_pattern: Pattern[str] | None = None
    custom_patterns: list[Pattern[str]] = field(default_factory=list)

    @classmethod
    def from_policy(cls, policy: MaskingPolicy) -> CompiledPolicy:
        """Compile regex patterns from policy."""
        # Hostname pattern - requires at least one letter to distinguish from IPs
        # Uses negative lookahead to exclude pure numeric segments like IP addresses
        hostname_re = (
            re.compile(
                r"\b(?!\d+\.\d+\.\d+\.\d+\b)"
                r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
                r"[a-zA-Z](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\b",
                re.IGNORECASE,
            )
            if policy.mask_hostnames
            else None
        )

        account_id_re = (
            re.compile(r"\b\d{12}\b")  # AWS account IDs are 12 digits
            if policy.mask_account_ids
            else None
        )

        # Cluster names: common patterns including prefixed names like prod-cluster-01
        # Matches: cluster-*, eks-*, k8s-*, kubernetes-*, and *-cluster-*, *-eks-* patterns
        cluster_name_re = (
            re.compile(
                r"\b(?:[a-zA-Z0-9]+[-_])?(?:cluster|eks|k8s|kubernetes)(?:[-_][a-zA-Z0-9]+)+\b",
                re.IGNORECASE,
            )
            if policy.mask_cluster_names
            else None
        )

        # Service names: common service naming patterns
        service_name_re = (
            re.compile(
                r"\b(?:service|svc|app|api|web|backend|frontend|worker|job)[-_][a-zA-Z0-9-_]+\b",
                re.IGNORECASE,
            )
            if policy.mask_service_names
            else None
        )

        # IPv4 address pattern - matches 0-255 for each octet
        ip_re = (
            re.compile(
                r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
            )
            if policy.mask_ip_addresses
            else None
        )

        email_re = (
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
            if policy.mask_emails
            else None
        )

        custom_res: list[Pattern[str]] = []
        for pattern in policy.custom_patterns:
            try:
                custom_res.append(re.compile(pattern))
            except re.error:
                continue  # Skip invalid regex patterns

        return cls(
            policy=policy,
            hostname_pattern=hostname_re,
            account_id_pattern=account_id_re,
            cluster_name_pattern=cluster_name_re,
            service_name_pattern=service_name_re,
            ip_address_pattern=ip_re,
            email_pattern=email_re,
            custom_patterns=custom_res,
        )
