"""Route registration: each Slack path uses the admission built for its payload shape.

Admission is unit-tested elsewhere. These tests exist because the two routes
once shared one handler, so every form-encoded button click was rejected by the
JSON events path — a defect invisible to admission-level tests.
"""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from config.constants.http import MAX_REQUEST_BODY_BYTES
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage.events.repository import InMemoryHandledSlackEventRepository
from gateway.transports.slack.settings import SlackGatewaySettings, SlackInboundTransport
from gateway.transports.slack.transport.events_api.receiver import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
)
from gateway.transports.slack.transport.events_api.server import (
    EVENTS_PATH,
    INTERACTIVITY_PATH,
    ListenerGate,
    build_slack_http_app,
)
from gateway.transports.slack.transport.events_api.signature import expected_signature

_SECRET = "8f742231b10e8888abcd99yyyzzz85a5"


def _now_timestamp() -> str:
    """Signed at request time, not import time.

    The signature check enforces a five-minute replay window, so a timestamp
    captured when the module loads is already stale by the time a full suite
    reaches these tests — they pass alone and fail in CI.
    """
    return str(int(time.time()))


def _settings() -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        signing_secret=_SECRET,
        inbound_transport=SlackInboundTransport.EVENTS_API_HTTP,
        allowed_user_ids=["U1"],
    )


def _client(submitted: list[Any]) -> TestClient:
    app = build_slack_http_app(
        settings=_settings(),
        approvals=ApprovalBroker(),
        handled_events=InMemoryHandledSlackEventRepository(),
        submit_turn=submitted.append,
        gate=ListenerGate(),
    )
    return TestClient(app)


def _headers(body: bytes) -> dict[str, str]:
    timestamp = _now_timestamp()
    return {
        SIGNATURE_HEADER: expected_signature(
            signing_secret=_SECRET, timestamp=timestamp, body=body
        ),
        TIMESTAMP_HEADER: timestamp,
    }


def test_interactivity_route_accepts_a_form_encoded_button_click() -> None:
    """A signed click must be accepted, not rejected as malformed JSON."""
    # Arrange.
    submitted: list[Any] = []
    click = {"type": "block_actions", "user": {"id": "U1"}, "actions": [{"action_id": "approve"}]}
    body = urlencode({"payload": json.dumps(click)}).encode()

    # Act.
    response = _client(submitted).post(INTERACTIVITY_PATH, content=body, headers=_headers(body))

    # Assert — accepted, and never routed to the turn executor.
    assert response.status_code == HTTPStatus.OK
    assert submitted == []


def test_events_route_submits_a_turn_for_a_signed_mention() -> None:
    # Arrange.
    submitted: list[Any] = []
    payload = {
        "type": "event_callback",
        "event_id": "Ev1",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@UBOT> status?",
            "channel": "C1",
            "channel_type": "channel",
            "ts": "1700000000.000100",
        },
    }
    body = json.dumps(payload).encode()

    # Act.
    response = _client(submitted).post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert.
    assert response.status_code == HTTPStatus.OK
    assert len(submitted) == 1


def test_unsigned_request_is_unauthorized_on_both_routes() -> None:
    # Arrange.
    submitted: list[Any] = []
    client = _client(submitted)
    bad = {SIGNATURE_HEADER: "v0=deadbeef", TIMESTAMP_HEADER: _now_timestamp()}

    # Act / Assert.
    for path, body in (
        (EVENTS_PATH, b'{"type":"event_callback"}'),
        (INTERACTIVITY_PATH, urlencode({"payload": "{}"}).encode()),
    ):
        response = client.post(path, content=body, headers=bad)
        assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert submitted == []


def test_url_verification_returns_the_challenge_body() -> None:
    # Arrange.
    body = json.dumps({"type": "url_verification", "challenge": "c-123"}).encode()

    # Act.
    response = _client([]).post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert.
    assert response.status_code == HTTPStatus.OK
    assert response.text == "c-123"


def test_event_gets_503_when_the_turn_executor_is_gone() -> None:
    """A listener outliving a failed start must not 500 on every delivery.

    Slack retries a 5xx either way, but 503 is the honest answer and keeps the
    replica's logs readable instead of a stack trace per event.
    """

    # Arrange — submit_turn behaves as a shut-down ThreadPoolExecutor does.
    def _dead_executor(_message: Any) -> None:
        raise RuntimeError("cannot schedule new futures after shutdown")

    app = build_slack_http_app(
        settings=_settings(),
        approvals=ApprovalBroker(),
        handled_events=InMemoryHandledSlackEventRepository(),
        submit_turn=_dead_executor,
        gate=ListenerGate(),
    )
    payload = {
        "type": "event_callback",
        "event_id": "Ev-dead",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@UBOT> status?",
            "channel": "C1",
            "channel_type": "channel",
            "ts": "1700000000.000100",
        },
    }
    body = json.dumps(payload).encode()

    # Act.
    response = TestClient(app).post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert.
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_a_503ed_event_is_not_swallowed_as_a_duplicate_on_retry() -> None:
    """Admission claims the id before dispatch; a failed dispatch must release it.

    Otherwise the retry is refused as a duplicate, answered 200, and the user's
    message is dropped with no turn ever running.
    """
    # Arrange — first delivery hits a dead executor, the retry finds a live one.
    dedup = InMemoryHandledSlackEventRepository()
    submitted: list[Any] = []
    alive = {"value": False}

    def _submit(message: Any) -> None:
        if not alive["value"]:
            raise RuntimeError("cannot schedule new futures after shutdown")
        submitted.append(message)

    app = build_slack_http_app(
        settings=_settings(),
        approvals=ApprovalBroker(),
        handled_events=dedup,
        submit_turn=_submit,
        gate=ListenerGate(),
    )
    payload = {
        "type": "event_callback",
        "event_id": "Ev-retry",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@UBOT> status?",
            "channel": "C1",
            "channel_type": "channel",
            "ts": "1700000000.000100",
        },
    }
    body = json.dumps(payload).encode()
    client = TestClient(app)

    # Act — the failed delivery, then Slack's retry once the replica recovers.
    first = client.post(EVENTS_PATH, content=body, headers=_headers(body))
    alive["value"] = True
    retry = client.post(EVENTS_PATH, content=body, headers=_headers(body))

    # Assert — the retry actually runs the turn.
    assert first.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert retry.status_code == HTTPStatus.OK
    assert len(submitted) == 1


class _ReleaseFailsDeduplicator(InMemoryHandledSlackEventRepository):
    """Leaves the provisional claim in place — Greptile's failed-release case."""

    def release(self, _event_id: str) -> bool:
        return False


def test_failed_release_does_not_permanently_drop_the_event_on_retry() -> None:
    """A claim that could not be released is recovered once it looks abandoned.

    Not immediately: while the window is open the claim means "a delivery is in
    flight", and reclaiming it then would queue the turn twice. The retry that
    matters arrives after Slack's retry delay, past the window.
    """
    clock = {"t": 0.0}
    dedup = _ReleaseFailsDeduplicator(now=lambda: clock["t"])
    submitted: list[Any] = []
    alive = {"value": False}

    def _submit(message: Any) -> None:
        if not alive["value"]:
            raise RuntimeError("cannot schedule new futures after shutdown")
        submitted.append(message)

    app = build_slack_http_app(
        settings=_settings(),
        approvals=ApprovalBroker(),
        handled_events=dedup,
        submit_turn=_submit,
        gate=ListenerGate(),
    )
    payload = {
        "type": "event_callback",
        "event_id": "Ev-release-fail",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U1",
            "text": "<@UBOT> status?",
            "channel": "C1",
            "channel_type": "channel",
            "ts": "1700000000.000100",
        },
    }
    body = json.dumps(payload).encode()
    client = TestClient(app)

    first = client.post(EVENTS_PATH, content=body, headers=_headers(body))
    alive["value"] = True
    immediate = client.post(EVENTS_PATH, content=body, headers=_headers(body))
    clock["t"] += 60.0  # past the abandonment window, as Slack's retry would be
    later = client.post(EVENTS_PATH, content=body, headers=_headers(body))

    # The failure is visible, the concurrent duplicate is refused, and the
    # delayed retry recovers the event.
    assert first.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert immediate.status_code == HTTPStatus.OK  # admitted-and-ignored duplicate
    assert later.status_code == HTTPStatus.OK
    assert len(submitted) == 1


def test_a_closed_listener_acts_on_nothing_on_either_route() -> None:
    """A uvicorn thread can outlive a failed start or a stop.

    Interactivity is handled inline, never through the executor, so without an
    explicit gate a residual listener would keep resolving approvals for a
    transport the gateway has already reported as unavailable.
    """
    # Arrange — the gate the failed-start and stop paths close.
    submitted: list[Any] = []
    gate = ListenerGate()
    approvals = ApprovalBroker()
    app = build_slack_http_app(
        settings=_settings(),
        approvals=approvals,
        handled_events=InMemoryHandledSlackEventRepository(),
        submit_turn=submitted.append,
        gate=gate,
    )
    client = TestClient(app)
    event_body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev-closed",
            "team_id": "T1",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "text": "<@UBOT> hi",
                "channel": "C1",
                "channel_type": "channel",
                "ts": "1700000000.000100",
            },
        }
    ).encode()
    click_body = urlencode(
        {"payload": json.dumps({"type": "block_actions", "user": {"id": "U1"}, "actions": []})}
    ).encode()

    # Act.
    gate.accepting = False
    event = client.post(EVENTS_PATH, content=event_body, headers=_headers(event_body))
    click = client.post(INTERACTIVITY_PATH, content=click_body, headers=_headers(click_body))

    # Assert — both refused, and no turn was queued.
    assert event.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert click.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert submitted == []


def test_oversized_event_body_is_rejected_before_signature_check() -> None:
    """The listener binds 0.0.0.0 and cannot authenticate without reading the body.

    Slack signs the raw bytes, so both routes must call ``request.body()``
    before they know who is calling. Without a cap above routing, any
    unauthenticated caller on the internet could make this host buffer a
    payload of their choosing.
    """
    submitted: list[Any] = []
    client = _client(submitted)

    resp = client.post(EVENTS_PATH, content=b"x" * (MAX_REQUEST_BODY_BYTES + 1))

    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert submitted == []
