"""Session resolution for polled inbound Buzz messages."""

from __future__ import annotations

from config.principal import StorageScope
from core.agent_harness import SessionCore
from gateway.core.middleware.inbound_decision import apply_inbound_decision
from gateway.core.storage import SessionResolver
from gateway.transports.buzz.inbound_security import InboundDecision
from gateway.transports.buzz.settings import BuzzInboundMessage
from integrations.buzz import BuzzClient
from integrations.messaging_security import MessagingPlatform


def conversation_key(event: BuzzInboundMessage) -> str:
    """Session-binding key: one session per ``(channel, sender)`` pair.

    Buzz channels have many members (unlike Telegram's 1:1 DMs), so the
    sender's pubkey alone cannot isolate sessions — two different people
    mentioning the bot in the same channel must not share one.
    """
    return f"{event.channel_id}:{event.pubkey}"


def resolve_or_rotate_session(
    event: BuzzInboundMessage,
    decision: InboundDecision,
    *,
    session_resolver: SessionResolver,
    client: BuzzClient,
    scope: StorageScope,
) -> SessionCore | None:
    """Apply inbound decision side effects, then resolve or rotate the REPL session."""

    def _send(text: str) -> None:
        client.send_message(channel=event.channel_id, content=text)

    return apply_inbound_decision(
        decision,
        platform=MessagingPlatform.BUZZ.value,
        resolver=session_resolver,
        scope=scope,
        conversation_key=conversation_key(event),
        chat_id=event.channel_id,
        text=event.content,
        send=_send,
    )


__all__ = ["conversation_key", "resolve_or_rotate_session"]
