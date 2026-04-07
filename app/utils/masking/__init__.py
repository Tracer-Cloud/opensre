"""Masking utilities for sensitive infrastructure identifiers.

This module provides tools to mask sensitive infrastructure identifiers
(cluster names, hostnames, account IDs, service names, etc.) before
sending data to external LLM models.

Environment Variables (all optional):
    Identifier Masking (default: all enabled):
        OPENSRE_MASK_HOSTNAMES=true|false      # Mask hostnames
        OPENSRE_MASK_ACCOUNT_IDS=true|false      # Mask AWS/GCP/Azure account IDs
        OPENSRE_MASK_CLUSTER_NAMES=true|false    # Mask cluster names
        OPENSRE_MASK_SERVICE_NAMES=true|false    # Mask service names
        OPENSRE_MASK_IP_ADDRESSES=true|false     # Mask IP addresses
        OPENSRE_MASK_EMAILS=true|false           # Mask email addresses
        OPENSRE_MASK_CUSTOM_PATTERNS="regex1,regex2"  # Custom regex patterns

    Performance & Safety:
        OPENSRE_MASK_MAX_PLACEHOLDERS=1000       # Max identifiers to mask (default: 1000)
        OPENSRE_MASK_VALIDATE_OUTPUT=true|false  # Validate LLM output placeholders (default: true)
        OPENSRE_MASK_PANIC_THRESHOLD=10        # Max validation errors before redaction (default: 10)

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

Safety Features:
    - Placeholder map size limit prevents unbounded memory growth
    - Validation detects broken/malformed placeholders in LLM output
    - Panic mode redacts output when excessive validation errors detected
    - All sensitive identifiers restored before user-facing display
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
from app.utils.masking.placeholder import PlaceholderMap
from app.utils.masking.policies import (
    CompiledPolicy,
    DetectedIdentifier,
    IdentifierType,
    MaskingPolicy,
    find_identifiers,
)
from app.utils.masking.validation import (
    PlaceholderIssue,
    ValidationSeverity,
    count_error_issues,
    get_unknown_placeholders,
    has_valid_placeholders,
    should_panic,
    summarize_issues,
    validate_placeholders,
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
    # Validation
    "PlaceholderIssue",
    "ValidationSeverity",
    "validate_placeholders",
    "has_valid_placeholders",
    "get_unknown_placeholders",
    "count_error_issues",
    "should_panic",
    "summarize_issues",
]
