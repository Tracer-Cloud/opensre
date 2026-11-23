"""Slack request-signature verification for Events API HTTP inbound.

Socket Mode authenticates once when the WebSocket opens. Over HTTP every
request must prove it came from Slack, so this is the transport's security
boundary: an unsigned or stale POST is an attacker, not a client.

Verification runs over the **raw** request body. Re-serializing parsed JSON
changes bytes (key order, separators) and the HMAC no longer matches, so the
route must hand the untouched bytes to :func:`verify_slack_signature`.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Slack signs `v0:<timestamp>:<body>`; the version prefix is part of the digest.
_SIGNATURE_VERSION = "v0"

# Slack's published replay window. A request older than this is refused even
# when the signature is valid, so a captured POST cannot be replayed later.
MAX_TIMESTAMP_AGE_SECONDS = 60 * 5


def signature_base_string(*, timestamp: str, body: bytes) -> bytes:
    """Return the exact byte string Slack computed its HMAC over."""
    return b":".join((_SIGNATURE_VERSION.encode(), timestamp.encode(), body))


def expected_signature(*, signing_secret: str, timestamp: str, body: bytes) -> str:
    """Return the ``v0=…`` signature Slack should have sent for this request."""
    digest = hmac.new(
        signing_secret.encode(),
        signature_base_string(timestamp=timestamp, body=body),
        hashlib.sha256,
    ).hexdigest()
    return f"{_SIGNATURE_VERSION}={digest}"


def timestamp_within_replay_window(timestamp: str, *, now: float) -> bool:
    """True when ``timestamp`` is recent enough to accept.

    A non-numeric timestamp is rejected rather than treated as 0, which would
    otherwise read as "very old" and give the same answer for the wrong reason.
    Future timestamps are bounded too — clock skew is small, forgery is not.
    """
    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError):
        return False
    return abs(now - sent_at) <= MAX_TIMESTAMP_AGE_SECONDS


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    now: float | None = None,
) -> bool:
    """True when this request is a fresh, correctly signed Slack delivery.

    Compared with :func:`hmac.compare_digest` so a wrong signature takes the
    same time to reject regardless of how much of it was right.
    """
    if not signing_secret or not signature or not timestamp:
        return False
    if not timestamp_within_replay_window(timestamp, now=time.time() if now is None else now):
        return False
    # Compared as bytes: a non-ASCII signature header is a wrong one, not a crash.
    return hmac.compare_digest(
        expected_signature(signing_secret=signing_secret, timestamp=timestamp, body=body).encode(),
        signature.encode(),
    )


__all__ = [
    "MAX_TIMESTAMP_AGE_SECONDS",
    "expected_signature",
    "signature_base_string",
    "timestamp_within_replay_window",
    "verify_slack_signature",
]
