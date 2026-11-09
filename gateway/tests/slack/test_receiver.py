"""Events API HTTP admission: signed, fresh, and exactly-once."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from gateway.core.storage.events.repository import InMemoryHandledSlackEventRepository
from gateway.transports.slack.transport.events_api.receiver import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SlackHttpStatus,
    admit_slack_http_request,
    admit_slack_interactivity_request,
)
from gateway.transports.slack.transport.events_api.signature import expected_signature

_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
_NOW = 1_700_000_000.0
_TIMESTAMP = str(int(_NOW))

_MENTION = {
    "type": "event_callback",
    "event_id": "Ev123",
    "team_id": "T1",
    "event": {
        "type": "app_mention",
        "user": "U1",
        "text": "<@UBOT> what is failing?",
        "channel": "C1",
        "channel_type": "channel",
        "ts": "1700000000.000100",
    },
}


def _admit(payload: dict[str, object], *, dedup: object | None = None, sign: bool = True):
    body = json.dumps(payload).encode()
    signature = (
        expected_signature(signing_secret=_SECRET, timestamp=_TIMESTAMP, body=body)
        if sign
        else "v0=deadbeef"
    )
    return admit_slack_http_request(
        headers={SIGNATURE_HEADER: signature, TIMESTAMP_HEADER: _TIMESTAMP},
        body=body,
        signing_secret=_SECRET,
        handled_events=dedup or InMemoryHandledSlackEventRepository(),
        now=_NOW,
    )


def test_accepts_a_signed_mention_and_returns_the_parsed_message() -> None:
    # Arrange / Act.
    outcome = _admit(_MENTION)

    # Assert.
    assert outcome.status is SlackHttpStatus.ACCEPTED
    assert outcome.message is not None
    assert outcome.message.channel_id == "C1"


def test_unsigned_request_is_rejected_before_any_parsing() -> None:
    """An attacker's payload must not reach the parser, let alone a turn."""
    # Arrange / Act.
    outcome = _admit(_MENTION, sign=False)

    # Assert.
    assert outcome.status is SlackHttpStatus.REJECTED
    assert outcome.message is None


def test_a_retried_delivery_does_not_start_a_second_turn() -> None:
    """Slack retries on a slow or failed answer; the user must not see two replies."""
    # Arrange — same event_id delivered twice after the first turn was queued.
    dedup = InMemoryHandledSlackEventRepository()

    # Act.
    first = _admit(_MENTION, dedup=dedup)
    assert first.status is SlackHttpStatus.ACCEPTED
    assert dedup.confirm(first.event_id) is True
    second = _admit(_MENTION, dedup=dedup)

    # Assert.
    assert second.status is SlackHttpStatus.IGNORED
    assert second.message is None


def test_url_verification_returns_the_challenge() -> None:
    # Arrange / Act.
    outcome = _admit({"type": "url_verification", "challenge": "abc123"})

    # Assert.
    assert outcome.status is SlackHttpStatus.CHALLENGE
    assert outcome.challenge == "abc123"


def test_event_callback_without_an_event_id_is_refused() -> None:
    """Without an id there is no dedup key, so it cannot be admitted safely."""
    # Arrange.
    payload = {key: value for key, value in _MENTION.items() if key != "event_id"}

    # Act / Assert.
    assert _admit(payload).status is SlackHttpStatus.REJECTED


def test_non_chat_events_are_ignored_not_rejected() -> None:
    """A verified Slack event we do not handle is not an auth failure."""
    # Arrange.
    payload = dict(_MENTION, event={"type": "reaction_added", "user": "U1"})

    # Act / Assert.
    assert _admit(payload).status is SlackHttpStatus.IGNORED


def _interactivity_body(payload: dict[str, object]) -> bytes:
    return urlencode({"payload": json.dumps(payload)}).encode()


def _admit_click(payload: dict[str, object], *, sign: bool = True):
    body = _interactivity_body(payload)
    signature = (
        expected_signature(signing_secret=_SECRET, timestamp=_TIMESTAMP, body=body)
        if sign
        else "v0=deadbeef"
    )
    return admit_slack_interactivity_request(
        headers={SIGNATURE_HEADER: signature, TIMESTAMP_HEADER: _TIMESTAMP},
        body=body,
        signing_secret=_SECRET,
        now=_NOW,
    )


_CLICK = {"type": "block_actions", "user": {"id": "U1"}, "actions": [{"action_id": "approve"}]}


def test_form_encoded_button_click_is_admitted_with_its_payload() -> None:
    """Slack posts interactivity as form-encoded ``payload=<json>``, not JSON.

    Routing clicks through the events path rejected every one of them, so
    approvals timed out and feedback was never recorded.
    """
    # Arrange / Act.
    outcome = _admit_click(_CLICK)

    # Assert.
    assert outcome.status is SlackHttpStatus.ACCEPTED
    assert outcome.payload is not None
    assert outcome.payload["type"] == "block_actions"


def test_unsigned_button_click_is_rejected() -> None:
    # Arrange / Act / Assert.
    assert _admit_click(_CLICK, sign=False).status is SlackHttpStatus.REJECTED


def test_overlapping_deliveries_only_admit_one_turn() -> None:
    """Two copies of one event in flight must not both queue a turn.

    A claim is provisional until the turn is dispatched. Reclaiming a
    provisional entry is only correct once it looks abandoned — doing it while
    the first delivery is still mid-flight duplicates the reply, the tool side
    effects, and the spend.
    """
    # Arrange — nothing confirmed yet, as during a concurrent duplicate.
    dedup = InMemoryHandledSlackEventRepository()

    # Act.
    first = dedup.claim("Ev-overlap")
    concurrent = dedup.claim("Ev-overlap")

    # Assert.
    assert first is True
    assert concurrent is False


def test_a_released_claim_is_available_again() -> None:
    """Release is the in-flight exit: the retry must be able to take it."""
    # Arrange / Act.
    dedup = InMemoryHandledSlackEventRepository()
    dedup.claim("Ev-released")
    dedup.release("Ev-released")

    # Assert.
    assert dedup.claim("Ev-released") is True
