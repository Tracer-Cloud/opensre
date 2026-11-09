"""Startup selects the configured inbound transport, and only that one."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from gateway.transports.slack import startup as startup_module
from gateway.transports.slack.settings import SlackGatewaySettings, SlackInboundTransport


def _settings(transport: SlackInboundTransport) -> SlackGatewaySettings:
    return SlackGatewaySettings(
        bot_token="xoxb-test",
        app_token="xapp-test",
        signing_secret="secret",
        inbound_transport=transport,
    )


class _StubWorker:
    def stop(self, *, _timeout: float = 8.0) -> bool:
        return True


@pytest.mark.parametrize(
    ("transport", "started", "not_started"),
    [
        (SlackInboundTransport.SOCKET_MODE, "socket", "http"),
        (SlackInboundTransport.EVENTS_API_HTTP, "http", "socket"),
    ],
)
def test_configured_transport_is_the_one_started(
    monkeypatch: pytest.MonkeyPatch,
    transport: SlackInboundTransport,
    started: str,
    not_started: str,
) -> None:
    """The other transport must not open a connection — two live consumers
    would split the Slack event stream between them."""
    # Arrange.
    calls: list[str] = []

    def _socket(**_kwargs: Any) -> _StubWorker:
        calls.append("socket")
        return _StubWorker()

    def _http(**_kwargs: Any) -> _StubWorker:
        calls.append("http")
        return _StubWorker()

    monkeypatch.setattr(startup_module, "_start_socket_mode", _socket)
    monkeypatch.setattr(startup_module, "_start_events_api_http", _http)
    monkeypatch.setattr(
        startup_module,
        "_TRANSPORT_STARTERS",
        {
            SlackInboundTransport.SOCKET_MODE: _socket,
            SlackInboundTransport.EVENTS_API_HTTP: _http,
        },
    )
    monkeypatch.setattr(startup_module, "load_slack_gateway_settings", lambda: _settings(transport))

    # Act.
    worker, settings = startup_module.start_slack_worker(
        logger=logging.getLogger("test"), handler=lambda *_a, **_k: None
    )

    # Assert.
    assert calls == [started]
    assert not_started not in calls
    assert settings.inbound_transport is transport
    assert worker is not None


def test_every_transport_has_a_starter() -> None:
    """A new enum member without a row would raise KeyError at boot."""
    # Arrange / Act / Assert.
    assert set(startup_module._TRANSPORT_STARTERS) == set(SlackInboundTransport)


def test_listener_bind_failure_is_a_transport_failure_not_a_crash() -> None:
    """A busy port must disable Slack, not abort the whole gateway.

    ``start_transports`` catches GatewayConfigurationError and
    GatewayTransportFailedError and keeps serving the other transports; any
    other exception escapes and takes the process down with it.
    """
    # Arrange — a listener thread that dies before ever binding.
    from concurrent.futures import ThreadPoolExecutor

    from gateway.core.lifecycle.errors import GatewayTransportFailedError
    from gateway.transports.slack.transport.events_api import server as events_server

    workers = ThreadPoolExecutor(max_workers=1)

    class _DeadServer:
        started = False
        servers: list[object] = []
        should_exit = False

        def run(self) -> None:  # never binds
            return None

    slow = _DeadServer()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(events_server.uvicorn, "Server", lambda _config: slow)
    monkeypatch.setattr(events_server.uvicorn, "Config", lambda *_a, **_k: object())

    # Act / Assert.
    try:
        with pytest.raises(GatewayTransportFailedError):
            events_server.serve_slack_http_in_thread(
                app=object(),  # never reached: the server stub does not bind
                port=1,
                workers=workers,
                gate=events_server.ListenerGate(),
                startup_timeout=0.2,
            )
        # The listener must be told to exit, or a bind completing after the
        # deadline keeps serving Slack with a dead executor behind it.
        assert slow.should_exit is True
    finally:
        monkeypatch.undo()
        workers.shutdown(wait=False)


def test_http_without_a_shared_store_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process-local dedup must be a decision, not a default.

    Each replica would keep its own handled-event set, so a Slack retry runs
    the turn twice — a warning log does not prevent that, a boot failure does.
    """
    # Arrange.
    from gateway.core.lifecycle.errors import GatewayConfigurationError

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv(startup_module.LOCAL_DEDUP_ENV, raising=False)

    # Act / Assert.
    with pytest.raises(GatewayConfigurationError, match="DATABASE_URL"):
        startup_module._build_handled_event_repository(logging.getLogger("test"))


def test_local_dedup_is_allowed_when_explicitly_waived(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(startup_module.LOCAL_DEDUP_ENV, "1")

    # Act.
    dedup = startup_module._build_handled_event_repository(logging.getLogger("test"))

    # Assert — a claim only refuses the retry once the turn was confirmed.
    assert dedup.claim("Ev1") is True
    dedup.confirm("Ev1")
    assert dedup.claim("Ev1") is False
