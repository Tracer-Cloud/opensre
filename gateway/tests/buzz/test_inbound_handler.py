from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from gateway.core.middleware.active_turns import ActiveTurnRegistry
from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz.inbound_handler import handle_polled_inbound_buzz_message
from gateway.transports.buzz.pending_approvals import PendingApprovals
from gateway.transports.buzz.settings import BuzzInboundMessage, GatewaySettings
from integrations.buzz.client import BuzzClient


@pytest.fixture(autouse=True)
def _no_integration_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the identity-policy lookup off the developer's real integration store."""
    from gateway.core.middleware import identity_policy

    monkeypatch.setattr(identity_policy, "get_integration", lambda *_a, **_kw: None)


def test_unauthorized_sender_never_invokes_the_callback() -> None:
    client = MagicMock(spec=BuzzClient)
    session_resolver = MagicMock()
    callback = MagicMock()
    executor = ThreadPoolExecutor(max_workers=1)

    try:
        asyncio.run(
            handle_polled_inbound_buzz_message(
                BuzzInboundMessage(
                    event_id="ev1",
                    pubkey="f" * 64,
                    channel_id="chan-1",
                    content="@bot hello",
                    created_at=100,
                    reply_event_ids=frozenset(),
                ),
                client=client,
                session_resolver=session_resolver,
                settings=GatewaySettings(private_key="k", allowed_pubkeys=["a" * 64]),
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(4),
                approvals=ApprovalBroker(),
                pending_approvals=PendingApprovals(),
                active_cancels=ActiveTurnRegistry(),
                handle_callback_to_gateway_agent=callback,
            )
        )
    finally:
        executor.shutdown(wait=True)

    callback.assert_not_called()
    session_resolver.resolve.assert_not_called()
