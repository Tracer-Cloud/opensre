"""Buzz turns are metered, and a denied turn never reaches the agent.

Buzz shipped as a copy of Telegram and inherited its missing credit gate, so
every turn ran for free. The charge is billed to the silo organization, not the
sender's pubkey: a charge posted against the wrong account must not admit work.
Hosted admission also fails closed whenever the ledger cannot be trusted.

The charge must also never be stranded. Buzz acknowledges a mention only once
its turn body has run, and shutdown cancels turns that outlast the drain
budget; a debit that lands on a mention which is then re-delivered charges the
organization twice for one message.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock

import pytest

from config.constants.gateway import CREDITS_DENIED_MESSAGE
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.tests.billing.turn_metering_harness import metered_callback
from gateway.transports.buzz import background, inbound_handler
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.inbound_security import InboundDecision
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings

TEST_ORG_ID = "org_buzz_credits"


class _FakeClient:
    """Records what the channel ends up showing, placeholder edits included."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.shown: list[str] = []

    def send_message(self, *, channel: str, content: str, **_kwargs: Any) -> dict[str, Any]:
        _ = channel
        self.sent.append(content)
        self.shown.append(content)
        return {"success": True, "error": "", "event_id": f"ev-{len(self.sent)}"}

    def edit_message(self, *, event_id: str, content: str) -> dict[str, Any]:
        _ = event_id
        self.shown.append(content)
        return {"success": True}


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
        "enforce_inbound_buzz_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )


def _event() -> BuzzInboundMessage:
    return BuzzInboundMessage(
        event_id="in-1",
        pubkey="npub-1",
        channel_id="chan-1",
        content="@bot hello",
        created_at=1,
        reply_event_ids=frozenset(),
    )


def _run_turn(client: _FakeClient, callback: MagicMock) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        asyncio.run(
            handle_polled_inbound_buzz_message(
                _event(),
                client=client,  # type: ignore[arg-type]
                session_resolver=_FakeSessionResolver(  # type: ignore[arg-type]
                    SessionCore(store=InMemorySessionStore())
                ),
                settings=GatewaySettings(private_key="k", allowed_pubkeys=["npub-1"]),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                active_cancels=ActiveTurnRegistry(),
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

    assert charges == [(TEST_ORG_ID, "buzz_turn")]
    callback.assert_not_called()
    assert client.shown[-1] == CREDITS_DENIED_MESSAGE


def test_the_ledger_is_never_charged_on_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow ledger must not stall polling or the other turns sharing the loop.

    Buzz dispatches turns as concurrent tasks on a single loop, so a POST made
    from that loop parks every other in-flight turn, and the poll loop with
    them, for the credits client timeout. Charging from the turn body keeps it
    on the executor. Telegram's twin inherits the same property.
    """
    loop_thread = threading.current_thread()
    charged_on: list[threading.Thread] = []

    def record_calling_thread(*_args: object, **_kwargs: object) -> CreditsOutcome:
        charged_on.append(threading.current_thread())
        return CreditsOutcome.ALLOWED

    monkeypatch.setattr(turn_metering, "consume_credits", record_calling_thread)

    _run_turn(_FakeClient(), MagicMock())

    assert charged_on, "the turn was never charged"
    assert charged_on[0] is not loop_thread, "the ledger POST would block the event loop"


def test_a_charged_turn_is_never_left_unacknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A debited ledger must not leave the mention eligible for re-delivery.

    Shutdown cancels turns that outlast the drain budget, and an unacknowledged
    mention is re-delivered on the next start — so a charge stranded by that
    cancellation is billed a second time for the same message. Cancelling the
    turn as the charge lands must therefore still acknowledge it.

    Awaiting the charge reopens this: the cancellation lands on the await, the
    coroutine unwinds past ``_dispatch_turn``'s trailing ``acknowledge`` (it
    catches ``Exception``, and ``CancelledError`` is not one), and the credit is
    spent on a mention that runs again.
    """
    charges: list[str] = []
    acked: list[BuzzInboundMessage] = []
    task_holder: list[asyncio.Task[None]] = []
    loop_holder: list[asyncio.AbstractEventLoop] = []
    cancelled = threading.Event()

    def charge_then_shut_down(_org: str, *, reason: str, **_kwargs: object) -> CreditsOutcome:
        """Debit the ledger, then expire the drain budget while still in flight.

        The cancel is posted to the loop the way ``_drain_active_turns`` issues
        it, and this call does not return until the loop has run it or the wait
        gives up. Awaiting the charge frees the loop to cancel mid-flight;
        charging inline holds it, so the cancel cannot land until the turn body
        has been handed to the executor.
        """
        charges.append(reason)
        loop_holder[0].call_soon_threadsafe(_cancel)
        cancelled.wait(0.25)
        return CreditsOutcome.ALLOWED

    def _cancel() -> None:
        task_holder[0].cancel()
        cancelled.set()

    monkeypatch.setattr(turn_metering, "consume_credits", charge_then_shut_down)

    async def _run() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            task = asyncio.get_running_loop().create_task(
                background._dispatch_turn(
                    _event(),
                    client=_FakeClient(),  # type: ignore[arg-type]
                    session_resolver=_FakeSessionResolver(  # type: ignore[arg-type]
                        SessionCore(store=InMemorySessionStore())
                    ),
                    settings=GatewaySettings(private_key="k", allowed_pubkeys=["npub-1"]),
                    executor=executor,
                    chat_locks={},
                    turn_semaphore=asyncio.Semaphore(1),
                    approvals=ApprovalBroker(),
                    pending_approvals=PendingApprovals(),
                    active_cancels=ActiveTurnRegistry(),
                    turn_cancel=None,
                    loop=asyncio.get_running_loop(),
                    handle_callback_to_gateway_agent=metered_callback(MagicMock()),
                    logger=logging.getLogger(__name__),
                    acknowledge=acked.append,
                )
            )
            task_holder.append(task)
            loop_holder.append(asyncio.get_running_loop())
            await asyncio.gather(task, return_exceptions=True)
        finally:
            executor.shutdown(wait=True)

    asyncio.run(_run())

    assert charges == ["buzz_turn"], "the ledger was debited exactly once"
    assert acked, "charged mention was left unacknowledged and will be charged again on replay"
