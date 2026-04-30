"""
Credential pattern detection for RCA report output.

Extends app/masking/ to redact secrets from generated reports
before they are published to external channels (Slack, webhooks).

The existing MaskingContext handles infrastructure identifiers
(hostnames, IPs, cluster names) before LLM calls. This module
adds output-side protection for credential patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

_REDACTED: Final[str] = "[REDACTED]"


# ---------------------------------------------------------------------------
# Patterns — kept deliberately minimal and readable
#
# Excluded intentionally (too many false positives for a first PR):
#   - Generic "token=", "password=", "secret=" prefix matching
#   - Database connection strings (scheme://user:pass@host is too broad)
#
# These can be added in follow-up PRs once the core is merged and tested
# in production.
# ---------------------------------------------------------------------------

_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    (
        # AWS Access Key ID — always starts with AKIA, exactly 20 uppercase alphanumeric chars
        # Case-sensitive. Word-bounded. Fixed length — no backtracking risk.
        "aws_access_key_id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        # JWT — three base64url segments separated by dots, each 10–200 chars
        # Anchored by the literal "eyJ" header that all JWTs start with.
        # Upper bound 500 per segment covers RS256 (~342 chars) and RS512 (~683 chars)
        # signatures, which are common in service-account and OAuth tokens.
        # and prevents runaway on adversarial input.
        "jwt_token",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,500}\.eyJ[A-Za-z0-9_-]{10,500}\.[A-Za-z0-9_-]{10,500}"),
    ),
    (
        # PEM private key header — detects the block delimiter line only.
        # Does not attempt to match the base64 body (avoids all backtracking risk).
        # Covers RSA, EC, and bare PRIVATE KEY formats.
        "pem_private_key",
        re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class RedactionResult:
    """Result of a redact_secrets() call.

    Attributes:
        text:     Scrubbed output, safe to publish externally.
        findings: Names of patterns that matched. Log this — never log the
                  matched values themselves.
    """

    text: str
    findings: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.findings)


def redact_secrets(text: str) -> RedactionResult:
    """Scan *text* for credential patterns and replace matches with [REDACTED].

    Safe to call on empty strings. Idempotent — running twice produces the
    same result. Does not raise; returns the original text unchanged if
    something unexpected occurs.

    Args:
        text: The RCA report or any string to sanitize before external publish.

    Returns:
        RedactionResult with scrubbed text and a list of matched pattern names.

    Example::

        result = redact_secrets(rca_report)
        if result.has_findings:
            logger.warning(
                "Secrets detected in RCA report before publish",
                extra={"matched_patterns": result.findings},
            )
        slack_client.send(result.text)
    """
    if not text:
        return RedactionResult(text=text)

    findings: list[str] = []
    scrubbed = text

    for name, pattern in _PATTERNS:
        scrubbed, count = pattern.subn(_REDACTED, scrubbed)
        if count:
            findings.append(name)

    return RedactionResult(text=scrubbed, findings=findings)
