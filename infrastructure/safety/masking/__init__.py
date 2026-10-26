"""Reversible masking of sensitive infrastructure identifiers.

Replaces pod names, cluster names, hostnames, account ids, service names,
IP addresses, and emails with stable placeholders (``<POD_0>``, ``<CLUSTER_1>``)
before sending text to external LLMs, and restores the originals in any
user-facing output. Complementary to ``infrastructure/safety/guardrails`` which performs
one-way redaction for hard-block rules.

Activated per operator by ``OPENSRE_MASK_ENABLED=true``. Off by default.
"""

from __future__ import annotations

from infrastructure.safety.masking.detectors import DetectedIdentifier, find_identifiers
from infrastructure.safety.masking.policy import ALL_KINDS, IdentifierKind, MaskingPolicy
from infrastructure.safety.masking.rules import MaskingRules

__all__ = [
    "ALL_KINDS",
    "DetectedIdentifier",
    "IdentifierKind",
    "MaskingPolicy",
    "MaskingRules",
    "find_identifiers",
]
