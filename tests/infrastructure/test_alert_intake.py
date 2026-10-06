"""The standalone alert-intake app (the interactive shell's listener)."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from config.constants.http import MAX_REQUEST_BODY_BYTES
from core.domain.alerts.inbox import AlertInbox, set_current_inbox
from infrastructure.alert_intake import build_alert_intake_app

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
    return TestClient(build_alert_intake_app(), client=_LOOPBACK, base_url="http://127.0.0.1")


def test_standalone_app_serves_only_health_and_alerts(client: TestClient) -> None:
    # The shell's listener does not carry the gateway's investigation routes.
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/investigate", json={"raw_alert": {}}).status_code == HTTPStatus.NOT_FOUND


def test_post_alert_queues_into_the_shared_inbox(client: TestClient, inbox: AlertInbox) -> None:
    resp = client.post("/alerts", json={"text": "CPU spike"})

    assert resp.status_code == HTTPStatus.ACCEPTED
    assert resp.json() == {"queued": True, "queue_depth": 1}
    queued = inbox.pop_nowait()
    assert queued is not None
    assert queued.text == "CPU spike"


def test_oversized_body_returns_413(client: TestClient) -> None:
    resp = client.post("/alerts", json={"text": "x" * (MAX_REQUEST_BODY_BYTES + 1)})
    assert resp.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_non_loopback_without_token_is_forbidden() -> None:
    remote = TestClient(build_alert_intake_app(), client=_REMOTE)
    assert remote.post("/alerts", json={"text": "x"}).status_code == HTTPStatus.FORBIDDEN


def test_loopback_post_from_another_site_is_forbidden(client: TestClient) -> None:
    # A page on any site can post to 127.0.0.1; its browser names the site in Origin.
    resp = client.post("/alerts", json={"text": "x"}, headers={"Origin": "https://example.com"})
    assert resp.status_code == HTTPStatus.FORBIDDEN


def test_loopback_post_with_a_rebound_host_is_forbidden() -> None:
    # DNS rebinding: the page is same-origin, so only the Host header shows the site.
    rebound = TestClient(build_alert_intake_app(), client=_LOOPBACK, base_url="http://example.com")
    assert rebound.post("/alerts", json={"text": "x"}).status_code == HTTPStatus.FORBIDDEN


def test_loopback_post_from_a_local_page_is_accepted(client: TestClient) -> None:
    resp = client.post("/alerts", json={"text": "x"}, headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == HTTPStatus.ACCEPTED
