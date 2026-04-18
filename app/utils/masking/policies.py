"""Masking policy models for sensitive infrastructure identifiers.

Policies are configurable via environment variables without code changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum, auto

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
    - OPENSRE_MASK_MAX_PLACEHOLDERS: maximum placeholders before pass-through (default: 1000)
    - OPENSRE_MASK_VALIDATE_OUTPUT: validate placeholders in output (default: true)
    - OPENSRE_MASK_PANIC_THRESHOLD: max validation errors before panic (default: 10)
    """

    mask_hostnames: bool = True
    mask_account_ids: bool = True
    mask_cluster_names: bool = True
    mask_service_names: bool = True
    mask_ip_addresses: bool = True
    mask_emails: bool = True
    custom_patterns: list[str] = field(default_factory=list)
    # Performance and safety settings
    max_placeholders: int = 1000
    validate_output: bool = True
    panic_threshold: int = 10

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

        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)).strip())
            except (ValueError, TypeError):
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
            max_placeholders=_env_int("OPENSRE_MASK_MAX_PLACEHOLDERS", 1000),
            validate_output=_env_bool("OPENSRE_MASK_VALIDATE_OUTPUT", True),
            panic_threshold=_env_int("OPENSRE_MASK_PANIC_THRESHOLD", 10),
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
    hostname_pattern: re.Pattern[str] | None = None
    account_id_pattern: re.Pattern[str] | None = None
    cluster_name_pattern: re.Pattern[str] | None = None
    service_name_pattern: re.Pattern[str] | None = None
    ip_address_pattern: re.Pattern[str] | None = None
    email_pattern: re.Pattern[str] | None = None
    custom_patterns: list[re.Pattern[str]] = field(default_factory=list)

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

        custom_res: list[re.Pattern[str]] = []
        for pattern in policy.custom_patterns:
            try:
                custom_res.append(re.compile(pattern))
            except re.error as e:
                raise ValueError(f"Invalid custom regex pattern '{pattern}': {e}") from e

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


# Cache for compiled policies to avoid recompilation overhead
# Simple dict with LRU-style eviction when size exceeds limit
_PolicyCacheKey = tuple[bool, bool, bool, bool, bool, bool, tuple[str, ...]]
_compiled_policy_cache: dict[_PolicyCacheKey, CompiledPolicy] = {}
_MAX_CACHE_SIZE = 32


def _make_policy_key(policy: MaskingPolicy) -> _PolicyCacheKey:
    """Create a hashable cache key from policy settings."""
    return (
        policy.mask_hostnames,
        policy.mask_account_ids,
        policy.mask_cluster_names,
        policy.mask_service_names,
        policy.mask_ip_addresses,
        policy.mask_emails,
        tuple(policy.custom_patterns),
    )


def get_compiled_policy(policy: MaskingPolicy) -> CompiledPolicy:
    """Get compiled policy with caching for performance.

    Compiled regex patterns are cached based on policy configuration
    to avoid recompilation overhead for repeated investigations with
    the same settings.

    Args:
        policy: Masking policy to compile

    Returns:
        CompiledPolicy with compiled regex patterns
    """
    global _compiled_policy_cache

    key = _make_policy_key(policy)

    # Check cache
    if key in _compiled_policy_cache:
        # Maintain true LRU semantics by moving the accessed key to the end.
        # Use sentinel to avoid race condition: if another thread evicts the key
        # between our check and pop, we'll get None and fall through to recompile.
        _sentinel = object()
        compiled = _compiled_policy_cache.pop(key, _sentinel)
        if compiled is not _sentinel:
            _compiled_policy_cache[key] = compiled
            return compiled
        # Another thread evicted the key between our check and pop; fall through to recompile.

    # Evict if at capacity (simple FIFO-style)
    if len(_compiled_policy_cache) >= _MAX_CACHE_SIZE:
        # Remove oldest entry (first in dict)
        oldest_key = next(iter(_compiled_policy_cache))
        del _compiled_policy_cache[oldest_key]

    # Compile and cache
    compiled = CompiledPolicy.from_policy(policy)
    _compiled_policy_cache[key] = compiled
    return compiled


def clear_compiled_policy_cache() -> None:
    """Clear the compiled policy cache.

    Useful for testing or when policy configurations change dynamically.
    """
    global _compiled_policy_cache
    _compiled_policy_cache.clear()


class IdentifierType(Enum):
    """Types of sensitive identifiers that can be detected."""

    HOSTNAME = auto()
    ACCOUNT_ID = auto()
    CLUSTER_NAME = auto()
    SERVICE_NAME = auto()
    IP_ADDRESS = auto()
    EMAIL = auto()
    CUSTOM = auto()


@dataclass(frozen=True)
class DetectedIdentifier:
    """A detected sensitive identifier in text.

    Attributes:
        identifier_type: The type of identifier detected
        value: The actual value found in the text
        start: Start position in the original text
        end: End position in the original text
    """

    identifier_type: IdentifierType
    value: str
    start: int
    end: int

    def __hash__(self) -> int:
        return hash((self.identifier_type, self.value, self.start, self.end))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DetectedIdentifier):
            return NotImplemented
        return (
            self.identifier_type == other.identifier_type
            and self.value == other.value
            and self.start == other.start
            and self.end == other.end
        )


def find_identifiers(
    text: str,
    hostname_pattern: re.Pattern[str] | None,
    account_id_pattern: re.Pattern[str] | None,
    cluster_name_pattern: re.Pattern[str] | None,
    service_name_pattern: re.Pattern[str] | None,
    ip_address_pattern: re.Pattern[str] | None,
    email_pattern: re.Pattern[str] | None,
    custom_patterns: list[re.Pattern[str]] | None = None,
) -> list[DetectedIdentifier]:
    """Find all sensitive identifiers in text.

    Args:
        text: The text to search
        hostname_pattern: Regex for hostnames
        account_id_pattern: Regex for account IDs
        cluster_name_pattern: Regex for cluster names
        service_name_pattern: Regex for service names
        ip_address_pattern: Regex for IP addresses
        email_pattern: Regex for email addresses
        custom_patterns: Additional custom regex patterns

    Returns:
        List of detected identifiers sorted by position
    """
    results: list[DetectedIdentifier] = []

    # Use list of tuples to avoid dict key collisions when patterns are None
    pattern_pairs: list[tuple[re.Pattern[str] | None, IdentifierType]] = [
        (hostname_pattern, IdentifierType.HOSTNAME),
        (account_id_pattern, IdentifierType.ACCOUNT_ID),
        (cluster_name_pattern, IdentifierType.CLUSTER_NAME),
        (service_name_pattern, IdentifierType.SERVICE_NAME),
        (ip_address_pattern, IdentifierType.IP_ADDRESS),
        (email_pattern, IdentifierType.EMAIL),
    ]

    for pattern, id_type in pattern_pairs:
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            results.append(
                DetectedIdentifier(
                    identifier_type=id_type,
                    value=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    if custom_patterns:
        for pattern in custom_patterns:
            for match in pattern.finditer(text):
                results.append(
                    DetectedIdentifier(
                        identifier_type=IdentifierType.CUSTOM,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )

    # Sort by position and remove overlapping matches (keep first occurrence)
    results.sort(key=lambda x: (x.start, x.end))
    filtered: list[DetectedIdentifier] = []
    last_end = -1
    for r in results:
        if r.start >= last_end:
            filtered.append(r)
            last_end = r.end

    return filtered
