"""Telegram and Buzz run one inbound turn in the same order.

The two polled transports assemble the same turn twice, in their own files.
Roughly 60 lines of that assembly are identical once the platform name is
masked, which is why consolidating them keeps being proposed — and why it needs
a guard first: an extraction must be provable not to reorder claim, timeout,
cancel tracking, dispatch or finalize for either transport.

This records what one turn does, through each transport's real handler, and
asserts the two recordings match. It is a characterization test: it pins today's
behaviour so a refactor has something to be equal to, not a behaviour anyone
designed from scratch.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import pytest

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz.turn_output import BuzzTurnOutput
from gateway.transports.telegram.turn_output import TelegramTurnOutput

_REAL_TRACK = ActiveTurnRegistry.track
_REAL_TELEGRAM_FINALIZE = TelegramTurnOutput.finalize
_REAL_BUZZ_FINALIZE = BuzzTurnOutput.finalize

# Happy-path order both handlers must produce. Reordering claim, timeout,
# cancel tracking or dispatch changes this list.
_HAPPY_PATH_STEPS = (
    "session_resolved",
    "timeout_armed",
    "cancel_tracked",
    "cancel_armed_before_turn",
    "session_bound_to_turn",
    "text_delivered",
    "turn_dispatched",
    "cancel_untracked",
    "timeout_disarmed",
    "terminal_claimed",
)


class _Recorder:
    """The ordered facts one turn produces, whichever transport ran it."""

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.turn_thread: str | None = None

    def note(self, step: str) -> None:
        self.steps.append(step)


class _RecordingResolver:
    def __init__(self, session: SessionCore, recorder: _Recorder) -> None:
        self._session = session
        self._recorder = recorder

    def resolve(self, **_kwargs: object) -> SessionCore:
        self._recorder.note("session_resolved")
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        return self._session


def _recording_callback(recorder: _Recorder):
    """A turn callback that records what the host handed it."""

    def _callback(text: str, session: Any, output: Any, _logger: logging.Logger) -> None:
        if isinstance(getattr(output, "turn_cancel", None), threading.Event):
            recorder.note("cancel_armed_before_turn")
        if session is not None:
            recorder.note("session_bound_to_turn")
        if text:
            recorder.note("text_delivered")
        recorder.turn_thread = threading.current_thread().name
        recorder.note("turn_dispatched")

    return _callback


def _instrument_host_sequence(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> ActiveTurnRegistry:
    """Record claim, cancel tracking, timeout arming, and reply finalize.

    The callback only sees what the host passed *into* the turn. Reordering
    ``track`` / ``timeout_after`` / ``claim`` / ``finalize`` around that
    callback would still match if we ignored those operations.
    """
    from gateway.core.middleware.active_turns import ActiveTurnRegistry
    from gateway.core.middleware.terminal_outcome import TerminalOutcomeArbiter
    from gateway.transports.buzz import inbound_handler as buzz_handler
    from gateway.transports.buzz.turn_output import BuzzTurnOutput
    from gateway.transports.telegram import inbound_handler as telegram_handler
    from gateway.transports.telegram.turn_output import TelegramTurnOutput

    class _RecordingTerminal(TerminalOutcomeArbiter):
        def claim(self) -> bool:
            won = super().claim()
            if won:
                recorder.note("terminal_claimed")
            return won

        @contextmanager
        def timeout_after(self, timeout_seconds: float, on_timeout: Any) -> Any:
            recorder.note("timeout_armed")
            with super().timeout_after(timeout_seconds, on_timeout):
                yield
            recorder.note("timeout_disarmed")

    monkeypatch.setattr(telegram_handler, "TerminalOutcomeArbiter", _RecordingTerminal)
    monkeypatch.setattr(buzz_handler, "TerminalOutcomeArbiter", _RecordingTerminal)

    registry = ActiveTurnRegistry()

    @contextmanager
    def recording_track(
        key: str,
        event: threading.Event,
        *,
        on_user_stop: Any = None,
    ) -> Any:
        recorder.note("cancel_tracked")
        with _REAL_TRACK(registry, key, event, on_user_stop=on_user_stop):
            yield
        recorder.note("cancel_untracked")

    monkeypatch.setattr(registry, "track", recording_track)

    def _wrap_finalize(cls: type, original: Any) -> None:
        def finalize(self: Any, answer: str) -> None:
            recorder.note("sink_finalized")
            original(self, answer)

        monkeypatch.setattr(cls, "finalize", finalize)

    _wrap_finalize(TelegramTurnOutput, _REAL_TELEGRAM_FINALIZE)
    _wrap_finalize(BuzzTurnOutput, _REAL_BUZZ_FINALIZE)
    return registry


class _TelegramClient:
    def send_chat_action(self, chat_id: str, action: str) -> None:
        pass

    def send_message(self, chat_id: str, text: str, **_kw: Any) -> tuple[bool, str, str]:
        _ = (chat_id, text)
        return True, "", "msg-1"

    def edit_message_text(
        self, chat_id: str, message_id: str, text: str, **_kw: Any
    ) -> tuple[bool, str]:
        _ = (chat_id, message_id, text)
        return True, ""


class _BuzzClient:
    def send_message(self, **_kw: Any) -> dict[str, Any]:
        return {"success": True, "error": "", "event_id": "ev-out"}

    def edit_message(self, **_kw: Any) -> dict[str, Any]:
        return {"success": True, "error": "", "event_id": "ev-out"}


@pytest.fixture(autouse=True)
def _isolated_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep identity policy off the developer's real integration store."""
    monkeypatch.setenv("ORGANIZATION_ID", "org_polled_parity")
    from gateway.core.middleware import identity_policy

    monkeypatch.setattr(identity_policy, "get_integration", lambda *_a, **_kw: None)


def _run_telegram_turn(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    from gateway.transports.telegram.inbound_handler import (
        handle_polled_inbound_telegram_message,
    )
    from gateway.transports.telegram.inbound_security import InboundDecision
    from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage

    registry = _instrument_host_sequence(monkeypatch, recorder)
    monkeypatch.setattr(
        "gateway.transports.telegram.inbound_handler.enforce_inbound_telegram_message_security",
        lambda **_kw: InboundDecision(allowed=True),
    )
    session = SessionCore(store=InMemorySessionStore())

    async def _run() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            await handle_polled_inbound_telegram_message(
                TelegramInboundMessage(
                    update_id=1,
                    user_id="user-1",
                    chat_id="chat-1",
                    message_id="m1",
                    text="hello",
                ),
                client=_TelegramClient(),  # type: ignore[arg-type]
                session_resolver=_RecordingResolver(session, recorder),  # type: ignore[arg-type]
                settings=GatewaySettings(
                    bot_token="tok",
                    allowed_user_ids=["user-1"],
                    turn_timeout_seconds=30.0,
                ),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                active_cancels=registry,
                handle_callback_to_gateway_agent=_recording_callback(recorder),
            )
        finally:
            executor.shutdown(wait=True)

    asyncio.run(_run())


def _run_buzz_turn(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
    from gateway.transports.buzz.inbound_security import InboundDecision
    from gateway.transports.buzz.pending_approvals import PendingApprovals
    from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings

    registry = _instrument_host_sequence(monkeypatch, recorder)
    monkeypatch.setattr(
        "gateway.transports.buzz.inbound_handler.enforce_inbound_buzz_message_security",
        lambda **_kw: InboundDecision(allowed=True),
    )
    session = SessionCore(store=InMemorySessionStore())
    sender = "a" * 64

    async def _run() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            await handle_polled_inbound_buzz_message(
                BuzzInboundMessage(
                    event_id="ev1",
                    pubkey=sender,
                    channel_id="chan-1",
                    content="@bot hello",
                    created_at=100,
                    reply_event_ids=frozenset(),
                ),
                client=_BuzzClient(),  # type: ignore[arg-type]
                session_resolver=_RecordingResolver(session, recorder),  # type: ignore[arg-type]
                settings=GatewaySettings(
                    private_key="k",
                    allowed_pubkeys=[sender],
                    turn_timeout_seconds=30.0,
                ),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                active_cancels=registry,
                handle_callback_to_gateway_agent=_recording_callback(recorder),
            )
        finally:
            executor.shutdown(wait=True)

    asyncio.run(_run())


def test_both_polled_transports_run_a_turn_in_the_same_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared sequence, recorded from each transport's real handler.

    Equality is the point: an extraction that reorders claim, timeout,
    cancel tracking, dispatch or finalize shows up as two different lists.
    """
    # Arrange
    telegram, buzz = _Recorder(), _Recorder()

    # Act
    _run_telegram_turn(monkeypatch, telegram)
    _run_buzz_turn(monkeypatch, buzz)

    # Assert
    assert telegram.steps == list(_HAPPY_PATH_STEPS)
    assert buzz.steps == list(_HAPPY_PATH_STEPS)
    assert "sink_finalized" not in telegram.steps


def test_both_polled_transports_run_the_turn_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn body runs on the executor, so polling is never blocked by a turn."""
    # Arrange
    telegram, buzz = _Recorder(), _Recorder()
    main_thread = threading.current_thread().name

    # Act
    _run_telegram_turn(monkeypatch, telegram)
    _run_buzz_turn(monkeypatch, buzz)

    # Assert
    assert telegram.turn_thread is not None
    assert telegram.turn_thread != main_thread
    assert buzz.turn_thread is not None
    assert buzz.turn_thread != main_thread
