"""``POST /v1/loops/sync``: a change saved in the web app reaches the gateway at once."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from gateway.core.lifecycle.controller import GatewayController
from gateway.web import webapp

_LOOPBACK = ("127.0.0.1", 40000)
_REMOTE = ("203.0.113.9", 40000)


class _CountingSync:
    """Stands in for the web app sync: counts passes and signals each one."""

    def __init__(self) -> None:
        self.passes = 0
        self.passed = threading.Event()

    def sync(self) -> bool:
        self.passes += 1
        self.passed.set()
        return False


@pytest.fixture
def controller(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[GatewayController, _CountingSync]]:
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    sync = _CountingSync()
    monkeypatch.setattr("bootstrap.adapters.webapp_loop_sync", lambda _runners=None: sync)
    started = GatewayController()
    started._start_webapp_loop_sync(logging.getLogger("test"))
    # What gateway.startup attaches when the gateway starts.
    webapp.app.state.loop_sync_wake = started.request_webapp_loop_sync
    try:
        yield started, sync
    finally:
        started._stopped.set()
        started._webapp_loop_sync_wake.set()
        if started._webapp_loop_sync_thread is not None:
            started._webapp_loop_sync_thread.join(timeout=5)
        if hasattr(webapp.app.state, "loop_sync_wake"):
            del webapp.app.state.loop_sync_wake


def test_a_nudge_runs_the_sync_now_instead_of_at_the_next_poll(
    controller: tuple[GatewayController, _CountingSync],
) -> None:
    # Arrange: the first pass runs at start; the next poll is minutes away.
    _gateway, sync = controller
    assert sync.passed.wait(timeout=5)
    sync.passed.clear()
    client = TestClient(webapp.app, client=_LOOPBACK, base_url="http://127.0.0.1")

    # Act
    answer = client.post("/v1/loops/sync")

    # Assert: accepted, and a second pass ran within moments.
    assert answer.status_code == HTTPStatus.ACCEPTED
    assert sync.passed.wait(timeout=5)
    assert sync.passes == 2


@pytest.mark.usefixtures("controller")
def test_a_nudge_needs_the_gateway_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("OPENSRE_ALERT_LISTENER_TOKEN", "secret")
    client = TestClient(webapp.app, client=_REMOTE, base_url="http://10.0.0.5")

    # Act
    refused = client.post("/v1/loops/sync")
    accepted = client.post("/v1/loops/sync", headers={"Authorization": "Bearer secret"})

    # Assert
    assert refused.status_code == HTTPStatus.UNAUTHORIZED
    assert accepted.status_code == HTTPStatus.ACCEPTED


def test_without_a_web_app_sync_the_nudge_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a gateway that is not a hosted organization's has nothing to sync.
    monkeypatch.delenv("OPENSRE_ALERT_LISTENER_TOKEN", raising=False)
    if hasattr(webapp.app.state, "loop_sync_wake"):
        del webapp.app.state.loop_sync_wake
    client = TestClient(webapp.app, client=_LOOPBACK, base_url="http://127.0.0.1")

    # Act
    answer = client.post("/v1/loops/sync")

    # Assert
    assert answer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert answer.json() == {"error": "loop_sync_unavailable"}


class _StopDuringClear(threading.Event):
    """A wake whose clear() lets ``stop()`` land first: the race the loop must survive."""

    def __init__(self, gateway: GatewayController) -> None:
        super().__init__()
        self._gateway = gateway

    def clear(self) -> None:
        # stop() sets _stopped, then wakes the thread, just before the clear erases it.
        self._gateway._stopped.set()
        self.set()
        super().clear()


def test_a_stop_that_lands_just_before_the_clear_still_ends_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    sync = _CountingSync()
    monkeypatch.setattr("bootstrap.adapters.webapp_loop_sync", lambda _runners=None: sync)
    gateway = GatewayController()
    gateway._webapp_loop_sync_wake = _StopDuringClear(gateway)

    # Act
    gateway._start_webapp_loop_sync(logging.getLogger("test"))
    thread = gateway._webapp_loop_sync_thread
    assert thread is not None
    thread.join(timeout=5)

    # Assert: the thread ended at once, without a pass or the five-minute sleep.
    assert not thread.is_alive()
    assert sync.passes == 0
