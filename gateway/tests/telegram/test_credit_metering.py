"""Telegram turns are metered, and a denied turn never reaches the agent.

Telegram shipped as a copy of the Slack path minus the credit gate, so every
turn ran for free. The charge must be billed to the silo organization, not the
Telegram user id — a per-chat charge would bill a non-existent account and pass
silently, since only an explicit 402 blocks a turn.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import CREDITS_DENIED_MESSAGE, TURN_ERROR_MESSAGE
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.telegram import inbound_handler
from gateway.transports.telegram.inbound_handler import handle_polled_inbound_telegram_message
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.settings import GatewaySettings, TelegramInboundMessage
from infrastructure.turn_host.status_messages import user_facing_error_message

TEST_ORG_ID = "org_tg_credits"


class _FakeClient:
    """Records what the chat ends up showing, placeholder edits included."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.shown: list[str] = []

    def send_chat_action(self, chat_id: str, action: str) -> None:
        _ = (chat_id, action)

    def send_message(self, chat_id: str, text: str, **_kwargs: Any) -> tuple[bool, str, str]:
        _ = chat_id
        self.sent.append(text)
        self.shown.append(text)
        return True, "", f"msg-{len(self.sent)}"

    def edit_message_text(
        self, chat_id: str, message_id: str, text: str, **_kwargs: Any
    ) -> tuple[bool, str]:
        _ = (chat_id, message_id)
        self.shown.append(text)
        return True, ""


class _FakeSessionResolver:
    def __init__(self, session: SessionCore) -> None:
        self._session = session

    def resolve(self, **_kwargs: object) -> SessionCore:
        return self._session

    def rotate(self, **_kwargs: object) -> SessionCore:
        return self._session


@pytest.fixture(autouse=True)
def _authorized_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", TEST_ORG_ID)
    monkeypatch.setattr(
        inbound_handler,
        "enforce_inbound_telegram_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )


def _run_turn(client: _FakeClient, callback: MagicMock) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        asyncio.run(
            handle_polled_inbound_telegram_message(
                TelegramInboundMessage(
                    update_id=1,
                    user_id="user-1",
                    chat_id="chat-1",
                    message_id="m1",
                    text="hello",
                ),
                client=client,  # type: ignore[arg-type]
                session_resolver=_FakeSessionResolver(SessionCore(store=InMemorySessionStore())),  # type: ignore[arg-type]
                settings=GatewaySettings(bot_token="tok", allowed_user_ids=["user-1"]),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                active_cancels=ActiveTurnRegistry(),
                # Production dispatch registers the cancel Event before the turn
                # exists; take that path so metering is pinned where it runs.
                turn_cancel=threading.Event(),
                handle_callback_to_gateway_agent=metered_callback(callback),
            )
        )
    finally:
        executor.shutdown(wait=True)


def test_denied_credits_stop_the_turn_and_bill_the_owning_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charges: list[tuple[str, str]] = []

    def deny(organization_id: str, *, reason: str, **_kwargs: object) -> CreditsOutcome:
        charges.append((organization_id, reason))
        return CreditsOutcome.DENIED

    monkeypatch.setattr(turn_metering, "consume_credits", deny)
    client = _FakeClient()
    callback = MagicMock()

    _run_turn(client, callback)

    assert charges == [(TEST_ORG_ID, "telegram_turn")]
    callback.assert_not_called()
    assert client.shown[-1] == CREDITS_DENIED_MESSAGE


@pytest.mark.parametrize("outcome", [CreditsOutcome.UNCONFIGURED, CreditsOutcome.UNAVAILABLE])
def test_untrustworthy_metering_fails_closed(
    monkeypatch: pytest.MonkeyPatch, outcome: CreditsOutcome
) -> None:
    monkeypatch.setattr(
        turn_metering,
        "consume_credits",
        lambda *_a, **_kw: outcome,
    )
    client = _FakeClient()
    callback = MagicMock()

    with pytest.raises(
        turn_metering.CreditMeteringUnavailableError,
        match="refusing unmetered work",
    ):
        _run_turn(client, callback)

    callback.assert_not_called()
    assert client.shown[-1] == user_facing_error_message(TURN_ERROR_MESSAGE)
