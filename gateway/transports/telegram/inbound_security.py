"""Inbound authorization helpers for the messaging gateway."""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import ROTATE_SESSION
from gateway.core.middleware.identity_policy import (
    load_identity_policy,
)
from gateway.transports.telegram.settings import connected_chat_user_id
from integrations.messaging_security import (
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    audit_log_inbound_message,
    authorize_inbound_message,
    complete_pairing,
    message_hash,
)


@dataclass(frozen=True)
class InboundDecision:
    """Authorization outcome for one inbound Telegram message."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def _is_connected_private_chat(
    policy: MessagingIdentityPolicy, connected: str, *, user_id: str, chat_id: str
) -> bool:
    """Whether this is the connection's own private chat, within the policy's limits."""
    return (
        bool(connected)
        and policy.inbound_enabled
        and user_id == connected
        and chat_id == connected
        and (not policy.allowed_chat_ids or chat_id in policy.allowed_chat_ids)
    )


def enforce_inbound_telegram_message_security(
    *,
    user_id: str,
    chat_id: str,
    text: str,
    env_allowed_user_ids: list[str],
) -> InboundDecision:
    """Authorize inbound Telegram DM text and handle /pair attempts."""
    record, policy = load_identity_policy(MessagingPlatform.TELEGRAM.value)
    if env_allowed_user_ids and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_user_ids)
        policy.inbound_enabled = True

    if text.strip().lower().startswith("/pair "):
        code = text.strip().split(maxsplit=1)[1] if " " in text.strip() else ""
        ok, msg = complete_pairing(policy=policy, user_id=user_id, code=code)
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=ok,
            reason=msg,
        )
        return InboundDecision(
            allowed=False,
            reply_text=msg,
            persist_policy=True,
            updated_policy=policy,
        )

    if text.strip().lower() in {"/start", "/help"}:
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="builtin command",
        )
        return InboundDecision(
            allowed=False,
            reply_text=(
                "OpenSRE Telegram gateway (DM text only).\n"
                "Send a message to chat with the agent.\n"
                "Commands: /new (new session), /help"
            ),
        )

    connected = connected_chat_user_id(record.get("credentials") or {}) if record else ""
    result: AuthorizationResult = (
        AuthorizationResult(allowed=True, reason="User is the connected chat")
        if _is_connected_private_chat(policy, connected, user_id=user_id, chat_id=chat_id)
        else authorize_inbound_message(
            policy=policy,
            user_id=user_id,
            chat_id=chat_id,
            message_text=text,
        )
    )

    if text.strip().lower() == "/new":
        if not result:
            audit_log_inbound_message(
                platform=MessagingPlatform.TELEGRAM.value,
                user_id=user_id,
                chat_id=chat_id,
                message_hash=message_hash(text),
                authorized=False,
                reason=result.reason,
            )
            return InboundDecision(allowed=False, reply_text=result.reason)
        audit_log_inbound_message(
            platform=MessagingPlatform.TELEGRAM.value,
            user_id=user_id,
            chat_id=chat_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="session rotate",
        )
        return InboundDecision(allowed=True, reply_text=ROTATE_SESSION)

    audit_log_inbound_message(
        platform=MessagingPlatform.TELEGRAM.value,
        user_id=user_id,
        chat_id=chat_id,
        message_hash=message_hash(text),
        authorized=bool(result),
        reason=result.reason,
    )
    if result:
        return InboundDecision(allowed=True)
    return InboundDecision(allowed=False, reply_text=result.reason)
