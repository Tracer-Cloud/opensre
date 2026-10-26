"""Socket Mode runs each Slack event once, however often Slack delivers it.

Slack delivers at least once: an envelope whose ack is lost comes back with
``retry_attempt`` set, and listener threads run concurrently, so two copies of
one event can be in flight together. With several turns running in parallel,
each copy would start its own investigation side by side.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.slack.processing.events import SlackInboundMessage
from gateway.transports.slack.settings import SlackGatewaySettings, SlackInboundTransport
from gateway.transports.slack.transport.socket_mode import worker as worker_module
from gateway.transports.slack.transport.socket_mode.dedupe import (
    BoundedHandledSlackEventRepository,
)
from gateway.transports.slack.transport.socket_mode.listener import SocketModeListener
from gateway.transports.slack.turn_stack import SlackTurnStack

_MESSAGE_TEXT = "why is checkout returning 502s"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _SocketClient:
    """Stands in for ``SocketModeClient``: records listeners and acks."""

    def __init__(self, **_kwargs: Any) -> None:
        self.socket_mode_request_listeners: list[SocketModeListener] = []
        self.acked: list[str] = []

    def connect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return True

    def send_socket_mode_response(self, response: SocketModeResponse) -> None:
        self.acked.append(response.envelope_id)


class _Heartbeat:
    def __init__(self, **_kwargs: Any) -> None:
        return None

    def start(self) -> None:
        return None


class _Executor:
    """Runs submitted work inline, or refuses it like an executor already shut down."""

    def __init__(self) -> None:
        self.refuse_next = False
        self.before_run: Callable[[], None] | None = None

    def submit(self, fn: Callable[..., object], /, *args: Any) -> None:
        if self.refuse_next:
            self.refuse_next = False
            raise RuntimeError("cannot schedule new futures after shutdown")
        hook, self.before_run = self.before_run, None
        if hook is not None:
            hook()
        fn(*args)


class _Dispatcher:
    def __init__(self) -> None:
        self.dispatched: list[SlackInboundMessage] = []

    def dispatch(self, inbound: SlackInboundMessage) -> None:
        self.dispatched.append(inbound)


def _start(
    monkeypatch: pytest.MonkeyPatch, executor: _Executor
) -> tuple[_SocketClient, SocketModeListener, _Dispatcher]:
    """Start the real worker over fakes and return the listener it installed."""
    dispatcher = _Dispatcher()
    stack = SlackTurnStack(
        web_client=MagicMock(),
        executor=executor,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        greeter=MagicMock(),
        approvals=ApprovalBroker(),
        bindings=MagicMock(),
        bot_user_id="UBOT",
    )
    client = _SocketClient()
    monkeypatch.setattr(worker_module, "build_slack_turn_stack", lambda **_kw: stack)
    monkeypatch.setattr(worker_module, "SocketModeClient", lambda **_kw: client)
    monkeypatch.setattr(worker_module, "ConnectionHeartbeat", _Heartbeat)
    worker_module.start_slack_gateway_background(
        settings=SlackGatewaySettings(
            bot_token="xoxb-test",
            app_token="xapp-test",
            inbound_transport=SlackInboundTransport.SOCKET_MODE,
        ),
        logger=logging.getLogger("gateway"),
        handler=lambda *_a, **_k: None,
    )
    (listener,) = client.socket_mode_request_listeners
    return client, listener, dispatcher


def _mention(
    envelope_id: str, *, retry_attempt: int | None = None, retry_reason: str | None = None
) -> SocketModeRequest:
    """One delivery of event ``Ev1``; each delivery has its own envelope id."""
    return SocketModeRequest(
        type="events_api",
        envelope_id=envelope_id,
        payload={
            "type": "event_callback",
            "event_id": "Ev1",
            "team_id": "T1",
            "event": {
                "type": "app_mention",
                "user": "U1",
                "channel": "C1",
                "ts": "1700000000.000100",
                "text": f"<@UBOT> {_MESSAGE_TEXT}",
            },
        },
        retry_attempt=retry_attempt,
        retry_reason=retry_reason,
    )


def test_a_redelivered_event_runs_one_turn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange.
    client, listener, dispatcher = _start(monkeypatch, _Executor())

    # Act — the original delivery, then Slack's retry of the same event.
    with caplog.at_level(logging.DEBUG, logger="gateway"):
        listener(client, _mention("env-1"))  # type: ignore[arg-type]
        listener(client, _mention("env-2", retry_attempt=1, retry_reason="timeout"))  # type: ignore[arg-type]

    # Assert — one turn; both envelopes acked, or Slack keeps redelivering;
    # the drop is logged by event id without the user's message.
    assert len(dispatcher.dispatched) == 1
    assert client.acked == ["env-1", "env-2"]
    assert "Ev1" in caplog.text
    assert _MESSAGE_TEXT not in caplog.text


def test_a_copy_arriving_while_the_first_is_being_queued_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Listener threads run concurrently: the claim is still provisional here."""
    # Arrange — the duplicate lands between the first copy's claim and confirm.
    executor = _Executor()
    client, listener, dispatcher = _start(monkeypatch, executor)
    executor.before_run = lambda: listener(client, _mention("env-2", retry_attempt=1))  # type: ignore[arg-type]

    # Act.
    listener(client, _mention("env-1"))  # type: ignore[arg-type]

    # Assert.
    assert len(dispatcher.dispatched) == 1


def test_a_claim_released_before_dispatch_lets_the_retry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise an event that never ran is refused forever as a duplicate."""
    # Arrange — the executor refuses the first delivery's work.
    executor = _Executor()
    client, listener, dispatcher = _start(monkeypatch, executor)
    executor.refuse_next = True

    # Act.
    listener(client, _mention("env-1"))  # type: ignore[arg-type]
    listener(client, _mention("env-2", retry_attempt=1, retry_reason="timeout"))  # type: ignore[arg-type]

    # Assert.
    assert len(dispatcher.dispatched) == 1


def test_a_handled_event_is_forgotten_after_the_ttl() -> None:
    # Arrange.
    clock = _Clock()
    handled = BoundedHandledSlackEventRepository(ttl_seconds=600, now=clock)
    assert handled.claim("Ev1") is True
    handled.confirm("Ev1")

    # Act / Assert — refused inside the window, admitted (and the entry gone) after it.
    clock.now = 599
    assert handled.claim("Ev1") is False
    clock.now = 600
    assert handled.claim("Ev1") is True


def test_the_cap_evicts_only_events_slack_can_no_longer_retry() -> None:
    # Arrange: a burst of three events inside one retry window, against a cap of two
    clock = _Clock()
    handled = BoundedHandledSlackEventRepository(
        max_events=2, retry_window_seconds=600, ttl_seconds=3600, now=clock
    )
    for event_id in ("Ev1", "Ev2", "Ev3"):
        handled.claim(event_id)
        handled.confirm(event_id)

    # Act: a retry of the first inside the window, then a new event after it
    retried_in_window = handled.claim("Ev1")
    clock.now = 600
    handled.claim("Ev4")
    handled.confirm("Ev4")

    # Assert: the burst kept every event Slack could still retry; once past the
    # window the oldest made room
    assert retried_in_window is False
    assert handled.claim("Ev1") is True
    assert handled.claim("Ev4") is False


def test_a_burst_past_the_hard_cap_still_bounds_memory() -> None:
    # Arrange: a cap of two, a hard cap of three, and a burst of four inside one window
    handled = BoundedHandledSlackEventRepository(
        max_events=2, hard_max_events=3, retry_window_seconds=600, now=_Clock()
    )
    for event_id in ("Ev1", "Ev2", "Ev3", "Ev4"):
        handled.claim(event_id)
        handled.confirm(event_id)

    # Act / Assert: only the oldest went, to stay within the hard cap. (A claim that
    # succeeds writes an entry, so the kept events are checked first.)
    assert handled.claim("Ev2") is False
    assert handled.claim("Ev4") is False
    assert handled.claim("Ev1") is True
