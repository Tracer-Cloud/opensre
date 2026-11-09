"""Buzz soft turn-timeout and /stop parity with the other chat transports.

Buzz was born as a copy of Telegram minus these exact features: a hung turn
hung its conversation forever, and nothing could stop a running turn. Twin of
``gateway/tests/telegram/test_turn_timeout.py``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.inbound_security import InboundDecision
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings


class _FakeClient:
    def __init__(self) -> None:
        self.edits: list[str] = []
        self.sent: list[str] = []
        self._msg = 0

    def send_message(self, *, channel: str, content: str) -> dict[str, Any]:
        _ = channel
        self.sent.append(content)
        self._msg += 1
        return {"success": True, "event_id": f"ev-{self._msg}"}

    def edit_message(self, *, event_id: str, content: str) -> dict[str, Any]:
        _ = event_id
        self.edits.append(content)
        return {"success": True}


class _FakeSessionResolver:
    def __init__(self, session: SessionCore) -> None:
        self._session = session

    def resolve(self, **_kwargs: object) -> SessionCore:
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        return self._session


def _settings(*, turn_timeout_seconds: float) -> GatewaySettings:
    return GatewaySettings(
        relay_url="wss://relay.test",
        private_key="nsec-test",
        allowed_pubkeys=["npub-1"],
        turn_timeout_seconds=turn_timeout_seconds,
    )


def _event(text: str = "hello") -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="in-1",
        pubkey="npub-1",
        channel_id="chan-1",
        content=text,
        created_at=1,
        reply_event_ids=frozenset(),
    )


@pytest.fixture
def org_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", "org_buzz_timeout")
    monkeypatch.setattr(
        "gateway.transports.buzz.inbound_handler.enforce_inbound_buzz_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )


@pytest.mark.usefixtures("org_env")
def test_turn_timeout_finalizes_placeholder_when_handler_hangs() -> None:
    """Soft timeout finalizes UX and sets sink.turn_cancel for cooperative stop."""
    client = _FakeClient()
    release = threading.Event()
    session = SessionCore(store=InMemorySessionStore())
    seen_cancel: list[threading.Event] = []

    def hanging_handler(
        _text: str,
        _session: Any,
        _sink: Any,
        _logger: logging.Logger,
    ) -> None:
        cancel = getattr(_sink, "turn_cancel", None)
        assert isinstance(cancel, threading.Event)
        seen_cancel.append(cancel)
        release.wait(5.0)

    async def _run() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            await handle_polled_inbound_buzz_message(
                _event(),
                client=client,  # type: ignore[arg-type]
                session_resolver=_FakeSessionResolver(session),  # type: ignore[arg-type]
                settings=_settings(turn_timeout_seconds=0.05),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                active_cancels=ActiveTurnRegistry(),
                handle_callback_to_gateway_agent=hanging_handler,
            )
        finally:
            release.set()
            executor.shutdown(wait=True, cancel_futures=True)

    # Drive the async path from a thread so a hang cannot block pytest forever
    # if the soft timeout fails to fire.
    done = threading.Event()
    error: list[Exception] = []

    def _thread() -> None:
        try:
            asyncio.run(_run())
        except Exception as exc:
            error.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=_thread)
    worker.start()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not any(
        "taking longer" in text.lower() for text in client.edits
    ):
        time.sleep(0.02)
    release.set()
    assert done.wait(5.0)
    worker.join(5.0)
    assert not error, error
    assert any("taking longer" in text.lower() for text in client.edits), client.edits
    assert seen_cancel and seen_cancel[0].is_set()


def test_gateway_settings_default_turn_timeout_matches_the_other_transports() -> None:
    settings = GatewaySettings(
        relay_url="wss://relay.test",
        private_key="nsec-test",
        allowed_pubkeys=["npub-1"],
    )

    assert settings.turn_timeout_seconds == 240.0


def test_stop_command_requests_cancel_of_the_running_turn() -> None:
    """A `/stop` in the conversation asks the active turn to stop, runs no turn."""
    from gateway.transports.buzz.background import maybe_handle_stop_command
    from gateway.transports.buzz.session_rotation import conversation_key

    registry = ActiveTurnRegistry()
    client = _FakeClient()
    stop_event = _event("/stop")
    cancel = threading.Event()

    with registry.track(conversation_key(stop_event), cancel):
        handled = maybe_handle_stop_command(stop_event, active_cancels=registry, client=client)

    assert handled is True
    assert cancel.is_set()


def test_stop_without_a_running_turn_reports_nothing_to_stop() -> None:
    from config.constants.gateway import NO_ACTIVE_TURN_MESSAGE
    from gateway.transports.buzz.background import maybe_handle_stop_command

    sent: list[str] = []

    class _Client:
        def send_message(self, *, channel: str, content: str) -> dict[str, Any]:
            _ = channel
            sent.append(content)
            return {"success": True, "event_id": "e"}

    handled = maybe_handle_stop_command(
        _event("/stop"), active_cancels=ActiveTurnRegistry(), client=_Client()
    )

    assert handled is True
    assert sent == [NO_ACTIVE_TURN_MESSAGE]


def test_a_normal_message_is_not_a_stop_command() -> None:
    from gateway.transports.buzz.background import maybe_handle_stop_command

    handled = maybe_handle_stop_command(
        _event("please stop the deploy"), active_cancels=ActiveTurnRegistry(), client=_FakeClient()
    )

    assert handled is False


def test_stop_in_the_same_batch_finds_the_dispatched_but_unstarted_turn() -> None:
    """Dispatch-time registration closes the create_task → track window."""
    from gateway.transports.buzz.background import maybe_handle_stop_command
    from gateway.transports.buzz.session_rotation import conversation_key

    # Arrange — the poll loop accepted a message and registered its cancel
    # Event, but the detached turn task has not run a single step yet
    registry = ActiveTurnRegistry()
    client = _FakeClient()
    message = _event("hello")
    accepted_cancel = threading.Event()
    registry.register(conversation_key(message), accepted_cancel)

    # Act — a /stop from the same poll batch is checked immediately
    handled = maybe_handle_stop_command(_event("/stop"), active_cancels=registry, client=client)

    # Assert — the accepted turn is cancelled; no "nothing to stop" reply
    assert handled is True
    assert accepted_cancel.is_set()
    assert client.sent == []


@pytest.mark.usefixtures("org_env")
def test_cancelled_before_start_reports_stopped_without_running_the_agent() -> None:
    """A turn whose dispatch-time cancel Event is already set never runs."""
    from config.constants.gateway import USER_STOP_MESSAGE
    from gateway.transports.buzz.session_rotation import conversation_key

    # Arrange — /stop already cancelled the turn while it was queued
    client = _FakeClient()
    session = SessionCore(store=InMemorySessionStore())
    registry = ActiveTurnRegistry()
    message = _event()
    turn_cancel = threading.Event()
    registry.register(conversation_key(message), turn_cancel)
    turn_cancel.set()
    agent_calls: list[str] = []
    acked: list[bool] = []

    def agent_handler(text: str, _session: Any, _sink: Any, _logger: logging.Logger) -> None:
        agent_calls.append(text)

    # Act
    async def _run() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            await handle_polled_inbound_buzz_message(
                message,
                client=client,  # type: ignore[arg-type]
                session_resolver=_FakeSessionResolver(session),  # type: ignore[arg-type]
                settings=_settings(turn_timeout_seconds=5.0),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                active_cancels=registry,
                turn_cancel=turn_cancel,
                handle_callback_to_gateway_agent=agent_handler,
                on_handled=lambda: acked.append(True),
            )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(_run())

    # Assert — placeholder edited to the stopped copy, message acknowledged,
    # agent never ran
    assert agent_calls == []
    assert client.edits == [USER_STOP_MESSAGE]
    assert acked == [True]
