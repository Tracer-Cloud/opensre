"""Masking utilities for sensitive infrastructure identifiers.

This module provides tools to mask sensitive infrastructure identifiers
(cluster names, hostnames, account IDs, service names, etc.) before
sending data to external LLM models.

All policies are configurable via environment variables without code changes:
    OPENSRE_MASK_HOSTNAMES=true|false      # Mask hostnames (default: true)
    OPENSRE_MASK_ACCOUNT_IDS=true|false    # Mask account IDs (default: true)
    OPENSRE_MASK_CLUSTER_NAMES=true|false  # Mask cluster names (default: true)
    OPENSRE_MASK_SERVICE_NAMES=true|false  # Mask service names (default: true)
    OPENSRE_MASK_IP_ADDRESSES=true|false   # Mask IP addresses (default: true)
    OPENSRE_MASK_EMAILS=true|false         # Mask emails (default: true)
    OPENSRE_MASK_CUSTOM_PATTERNS="regex1,regex2"  # Custom regex patterns

Example:
    from app.utils.masking import MaskingContext, mask_text, unmask_text

    # Create context for an investigation
    ctx = MaskingContext.create()

    # Mask text before sending to LLM
    masked = ctx.mask_text("Error in prod-cluster-01: connection to api.example.com failed")
    # Result: "Error in <CLUSTER_0>: connection to <HOSTNAME_0> failed"

    # Unmask LLM response
    unmasked = ctx.unmask_text("Check logs for <CLUSTER_0>")
    # Result: "Check logs for prod-cluster-01"
"""

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
from app.utils.masking.detectors import (
    DetectedIdentifier,
    IdentifierType,
    find_identifiers,
)
from app.utils.masking.placeholder import PlaceholderMap
from app.utils.masking.policies import (
    CompiledPolicy,
    MaskingPolicy,
)

__all__ = [
    # Core utilities
    "MaskingContext",
    "mask_text",
    "unmask_text",
    "mask_dict",
    "mask_list",
    "unmask_dict",
    "unmask_list",
    "reset_global_context",
    # Policy configuration
    "MaskingPolicy",
    "CompiledPolicy",
    # Detection
    "DetectedIdentifier",
    "IdentifierType",
    "find_identifiers",
    # Placeholder mapping
    "PlaceholderMap",
]
