"""Shared credential redaction helpers for outbound message delivery."""

from __future__ import annotations

import re

REDACTED = "<redacted>"

_SLACK_ACCESS_TOKEN_RE = re.compile(r"(xox(?:[baprs]-|e\.xoxp-))[A-Za-z0-9-]+")


def redact_token(text: str, token: str) -> str:
    """Replace a known credential with ``<redacted>`` in *text*."""
    if token and token in text:
        return text.replace(token, REDACTED)
    return text


def redact_slack_token(text: str, access_token: str) -> str:
    """Replace ``access_token`` and scrub Slack ``xox*-`` token patterns from *text*."""
    redacted = redact_token(text, access_token)
    return _SLACK_ACCESS_TOKEN_RE.sub(rf"\1{REDACTED}", redacted)
