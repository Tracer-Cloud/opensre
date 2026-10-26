"""Tests for the ``POST /alerts`` intake on the gateway web app."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from config.constants.http import MAX_REQUEST_BODY_BYTES
from core.domain.alerts.inbox import AlertInbox, set_current_inbox
from gateway.web import webapp

_LOOPBACK = ("127.0.0.1", 40000)
_REMOTE = ("203.0.113.9", 40000)


@pytest.fixture(autouse=True)
def inbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[AlertInbox]:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    box = AlertInbox(maxsize=3)
    set_current_inbox(box)
    yield box
    set_current_inbox(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(webapp.app, client=_LOOPBACK, base_url="http://127.0.0.1")


def test_healthz_is_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"status": "ok"}


def test_post_alert_queues_and_returns_202(client: TestClient, inbox: AlertInbox) -> None:
    resp = client.post("/alerts", json={"text": "CPU spike"})

    assert resp.status_code == HTTPStatus.ACCEPTED
    assert resp.json() == {"queued": True, "queue_depth": 1}
    queued = inbox.pop_nowait()
    assert queued is not None
    assert queued.text == "CPU spike"
    assert queued.received_at is not None


def test_overflow_reports_dropped(client: TestClient) -> None:
    for i in range(4):
        resp = client.post("/alerts", json={"text": f"alert {i}"})
    assert resp.status_code == HTTPStatus.ACCEPTED
    body = resp.json()
    assert body["dropped"] == 1
    assert body["warning"] == "inbox full, oldest alert dropped"


def test_invalid_json_returns_400(client: TestClient) -> None:
    resp = client.post("/alerts", content=b"not json")
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert resp.json() == {"error": "invalid json"}


def test_missing_text_returns_400(client: TestClient, inbox: AlertInbox) -> None:
    assert client.post("/alerts", json={}).status_code == HTTPStatus.BAD_REQUEST
    assert inbox.pop_nowait() is None


def test_rejected_alert_does_not_leak_exception_detail(client: TestClient) -> None:
    # Arrange: omit required ``text`` and plant a value pydantic would echo
    # verbatim (``input_value=...``) inside the raw ValidationError message.
    payload = {"severity": "SENSITIVE-DO-NOT-LEAK"}

    # Act
    resp = client.post("/alerts", json=payload)

    # Assert: only the exception type is returned; no detail reaches the caller.
    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert resp.json() == {"error": "invalid alert payload: ValidationError"}
    body = resp.text
    assert "SENSITIVE-DO-NOT-LEAK" not in body
    assert "IncomingAlert" not in body
    assert "Field required" not in body


def test_oversized_body_returns_413(client: TestClient, inbox: AlertInbox) -> None:
    resp = client.post("/alerts", json={"text": "x" * (MAX_REQUEST_BODY_BYTES + 1)})
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert resp.json() == {"error": "payload too large"}
    assert inbox.pop_nowait() is None


def test_non_loopback_without_token_returns_403(inbox: AlertInbox) -> None:
    remote = TestClient(webapp.app, client=_REMOTE)
    resp = remote.post("/alerts", json={"text": "x"})
    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert inbox.pop_nowait() is None


def test_token_auth(monkeypatch: pytest.MonkeyPatch, inbox: AlertInbox) -> None:
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", "sekret")
    remote = TestClient(webapp.app, client=_REMOTE)

    assert remote.post("/alerts", json={"text": "x"}).status_code == HTTPStatus.UNAUTHORIZED
    assert (
        remote.post(
            "/alerts", json={"text": "x"}, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == HTTPStatus.UNAUTHORIZED
    )
    assert (
        remote.post(
            "/alerts", json={"text": "x"}, headers={"Authorization": "Bearer sekret"}
        ).status_code
        == HTTPStatus.ACCEPTED
    )
    assert inbox.pop_nowait() is not None
