"""Events API HTTP inbound: verify, de-duplicate, and normalize one request.

Framework-agnostic on purpose: it takes headers and raw bytes and returns a
typed outcome, so admission is testable without a server. The Slack transport
serves these routes on its own port (:mod:`.server`) rather than sharing
``gateway.web`` — that app is the health/alert surface and must not import a
chat transport (package DAG, pinned by ``test_package_borders``).

The caller must answer Slack within three seconds or the delivery is retried,
so an accepted request is only *parsed* here — running the turn belongs to a
worker the caller hands it to.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs

from gateway.core.storage.events.repository import HandledSlackEventRepository
from gateway.transports.slack.processing.events import SlackInboundMessage, parse_events_api_payload
from gateway.transports.slack.transport.events_api.signature import verify_slack_signature

SIGNATURE_HEADER = "x-slack-signature"
TIMESTAMP_HEADER = "x-slack-request-timestamp"
# Present only on a retry; Slack counts from 1.
RETRY_HEADER = "x-slack-retry-num"

_URL_VERIFICATION_TYPE = "url_verification"
_EVENT_CALLBACK_TYPE = "event_callback"

# Kept in step with the Postgres store so single-replica and multi-replica
# deployments admit and refuse the same deliveries.


class SlackHttpStatus(StrEnum):
    """What the caller should do with a delivered request."""

    #: Answer with ``challenge`` — Slack is registering the request URL.
    CHALLENGE = "challenge"
    #: Signature missing, wrong, or replayed. Answer 401 and run nothing.
    REJECTED = "rejected"
    #: Verified, but no turn to run (already handled, or not a chat event).
    IGNORED = "ignored"
    #: Verified and parsed — hand ``message`` to a worker, then answer 200.
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class SlackHttpOutcome:
    """Result of admitting one HTTP delivery."""

    status: SlackHttpStatus
    challenge: str = ""
    message: SlackInboundMessage | None = None
    #: Set on ACCEPTED so a caller that fails to start the turn can release
    #: the claim; otherwise the retry is refused and the event is lost.
    event_id: str = ""
    reason: str = ""


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header read — HTTP header casing is not guaranteed."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _decoded_payload(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class SlackInteractivityOutcome:
    """Result of admitting one interactive (button click) delivery."""

    status: SlackHttpStatus
    payload: Mapping[str, Any] | None = None
    reason: str = ""


def admit_slack_interactivity_request(
    *,
    headers: Mapping[str, str],
    body: bytes,
    signing_secret: str,
    now: float | None = None,
) -> SlackInteractivityOutcome:
    """Verify one interactive delivery and return its decoded payload.

    Slack posts button clicks form-encoded as ``payload=<json>``, not as a JSON
    document, and they carry no ``event_id`` — so they share the signature check
    with events but nothing else. Clicks are not de-duplicated: resolving an
    approval twice is idempotent at the broker, while dropping one strands a
    turn waiting on a decision that already happened.
    """
    if not verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=_header(headers, TIMESTAMP_HEADER),
        signature=_header(headers, SIGNATURE_HEADER),
        body=body,
        now=now,
    ):
        return SlackInteractivityOutcome(
            SlackHttpStatus.REJECTED, reason="signature verification failed"
        )

    encoded = parse_qs(body.decode("utf-8", errors="replace")).get("payload")
    if not encoded:
        return SlackInteractivityOutcome(
            SlackHttpStatus.IGNORED, reason="no payload field in form body"
        )
    try:
        payload = json.loads(encoded[0])
    except ValueError:
        return SlackInteractivityOutcome(
            SlackHttpStatus.REJECTED, reason="payload field is not JSON"
        )
    if not isinstance(payload, dict):
        return SlackInteractivityOutcome(
            SlackHttpStatus.REJECTED, reason="payload is not an object"
        )
    return SlackInteractivityOutcome(SlackHttpStatus.ACCEPTED, payload=payload)


def admit_slack_http_request(
    *,
    headers: Mapping[str, str],
    body: bytes,
    signing_secret: str,
    handled_events: HandledSlackEventRepository,
    now: float | None = None,
    open_channel_ids: Collection[str] = (),
) -> SlackHttpOutcome:
    """Verify one Slack HTTP delivery and return what the caller should do.

    ``body`` must be the raw request bytes: the signature covers them exactly,
    and re-serialized JSON will not match.
    """
    if not verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=_header(headers, TIMESTAMP_HEADER),
        signature=_header(headers, SIGNATURE_HEADER),
        body=body,
        now=now,
    ):
        return SlackHttpOutcome(SlackHttpStatus.REJECTED, reason="signature verification failed")

    payload = _decoded_payload(body)
    if payload is None:
        return SlackHttpOutcome(SlackHttpStatus.REJECTED, reason="body is not a JSON object")

    payload_type = str(payload.get("type") or "")
    if payload_type == _URL_VERIFICATION_TYPE:
        return SlackHttpOutcome(
            SlackHttpStatus.CHALLENGE, challenge=str(payload.get("challenge") or "")
        )
    if payload_type != _EVENT_CALLBACK_TYPE:
        return SlackHttpOutcome(SlackHttpStatus.IGNORED, reason=f"unhandled type {payload_type!r}")

    # Dedup before parsing: a retry of an event already running must not start
    # a second turn, and the user sees the duplicate if it does.
    event_id = str(payload.get("event_id") or "")
    if not event_id:
        return SlackHttpOutcome(SlackHttpStatus.REJECTED, reason="event_callback without event_id")
    if not handled_events.claim(event_id):
        return SlackHttpOutcome(SlackHttpStatus.IGNORED, reason="duplicate delivery")

    message = parse_events_api_payload(
        payload,
        open_channel_ids=open_channel_ids,
    )
    if message is None:
        return SlackHttpOutcome(SlackHttpStatus.IGNORED, reason="not a chat event")
    return SlackHttpOutcome(SlackHttpStatus.ACCEPTED, message=message, event_id=event_id)


__all__ = [
    "RETRY_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "SlackHttpOutcome",
    "SlackInteractivityOutcome",
    "SlackHttpStatus",
    "admit_slack_http_request",
    "admit_slack_interactivity_request",
]
