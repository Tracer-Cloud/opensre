"""Telegram soft turn-timeout parity with Slack/Discord."""

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
from gateway.transports.telegram.inbound_handler import handle_polled_inbound_telegram_message
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage


class _FakeClient:
    def __init__(self) -> None:
        self.edits: list[str] = []
        self._msg = 0

    def send_chat_action(self, chat_id: str, action: str) -> None:
        _ = (chat_id, action)

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        _ = (chat_id, text, parse_mode, reply_markup)
        self._msg += 1
        return True, "", f"msg-{self._msg}"

    def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        _ = (chat_id, message_id, parse_mode, reply_markup)
        self.edits.append(text)
        return True, ""


class _FakeSessionResolver:
    def __init__(self, session: SessionCore) -> None:
        self._session = session

    def resolve(self, **_kwargs: object) -> SessionCore:
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        return self._session


def _settings(*, turn_timeout_seconds: float) -> GatewaySettings:
    return GatewaySettings(
        bot_token="tok",
        allowed_user_ids=["user-1"],
        turn_timeout_seconds=turn_timeout_seconds,
    )


@pytest.fixture
def org_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", "org_tg_timeout")
    monkeypatch.setattr(
        "gateway.transports.telegram.inbound_handler.enforce_inbound_telegram_message_security",
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
            await handle_polled_inbound_telegram_message(
                TelegramInboundMessage(
                    update_id=1,
                    user_id="user-1",
                    chat_id="chat-1",
                    message_id="m1",
                    text="hello",
                ),
                client=client,  # type: ignore[arg-type]
                session_resolver=_FakeSessionResolver(session),
                settings=_settings(turn_timeout_seconds=0.05),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                active_cancels=ActiveTurnRegistry(),
                handle_callback_to_gateway_agent=hanging_handler,
            )
        finally:
            release.set()
            executor.shutdown(wait=True, cancel_futures=True)

    # Drive the async path from a thread so the hang cannot block pytest forever
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


def test_gateway_settings_default_turn_timeout_matches_slack() -> None:
    settings = GatewaySettings(bot_token="tok")
    assert settings.turn_timeout_seconds == 240.0
