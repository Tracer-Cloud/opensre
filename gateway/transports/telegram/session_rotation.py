"""Session resolution for polled inbound Telegram messages."""

from __future__ import annotations

from config.principal import StorageScope
from core.agent_harness import SessionCore
from gateway.core.middleware.inbound_decision import apply_inbound_decision
from gateway.core.storage import SessionResolver
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.settings import TelegramInboundMessage
from integrations.messaging_security import MessagingPlatform


def resolve_or_rotate_session(
    event: TelegramInboundMessage,
    decision: InboundDecision,
    *,
    session_resolver: SessionResolver,
    client: TelegramBotClient,
    scope: StorageScope,
) -> SessionCore | None:
    """Apply inbound decision side effects, then resolve or rotate the REPL session."""

    def _send(text: str) -> None:
        client.send_message(event.chat_id, text)

    return apply_inbound_decision(
        decision,
        platform=MessagingPlatform.TELEGRAM.value,
        resolver=session_resolver,
        scope=scope,
        conversation_key=event.user_id,
        chat_id=event.chat_id,
        text=event.text,
        send=_send,
    )


__all__ = ["resolve_or_rotate_session"]
