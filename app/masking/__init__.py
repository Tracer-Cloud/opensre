"""Reversible masking of sensitive infrastructure identifiers.

Replaces pod names, cluster names, hostnames, account ids, service names,
IP addresses, and emails with stable placeholders (``<POD_0>``, ``<CLUSTER_1>``)
before sending text to external LLMs, and restores the originals in any
user-facing output. Complementary to ``app/guardrails`` which performs
one-way redaction for hard-block rules.

Activated per operator by ``OPENSRE_MASK_ENABLED=true``. Off by default.
"""

from __future__ import annotations

from app.masking.context import MaskingContext
from app.masking.detectors import DetectedIdentifier, find_identifiers
from app.masking.policy import ALL_KINDS, IdentifierKind, MaskingPolicy

__all__ = [
    "ALL_KINDS",
    "DetectedIdentifier",
    "IdentifierKind",
    "MaskingContext",
    "MaskingPolicy",
    "find_identifiers",
]
