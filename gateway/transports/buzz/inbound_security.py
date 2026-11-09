"""Inbound authorization helpers for the Buzz gateway.

Mirrors :mod:`gateway.transports.telegram.inbound_security` — the same
:mod:`integrations.messaging_security` primitives, keyed on the sender's
Nostr pubkey (``user_id``) and the Buzz channel UUID (``chat_id``) instead of
a numeric Telegram id and chat id.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.gateway import ROTATE_SESSION
from gateway.core.middleware.identity_policy import (
    load_identity_policy,
)
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
    """Authorization outcome for one inbound Buzz mention/reply."""

    allowed: bool
    reply_text: str = ""
    persist_policy: bool = False
    updated_policy: MessagingIdentityPolicy | None = None


def enforce_inbound_buzz_message_security(
    *,
    pubkey: str,
    channel_id: str,
    text: str,
    env_allowed_pubkeys: list[str],
) -> InboundDecision:
    """Authorize an inbound Buzz mention/reply and handle ``/pair`` attempts."""
    record, policy = load_identity_policy(MessagingPlatform.BUZZ.value)
    if env_allowed_pubkeys and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_pubkeys)
        policy.inbound_enabled = True

    if text.strip().lower().startswith("/pair "):
        code = text.strip().split(maxsplit=1)[1] if " " in text.strip() else ""
        ok, msg = complete_pairing(policy=policy, user_id=pubkey, code=code)
        audit_log_inbound_message(
            platform=MessagingPlatform.BUZZ.value,
            user_id=pubkey,
            chat_id=channel_id,
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
            platform=MessagingPlatform.BUZZ.value,
            user_id=pubkey,
            chat_id=channel_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="builtin command",
        )
        return InboundDecision(
            allowed=False,
            reply_text=(
                "OpenSRE Buzz gateway. @mention me to chat, or reply in-thread to continue.\n"
                "Commands: /new (new session), /help"
            ),
        )

    result: AuthorizationResult = authorize_inbound_message(
        policy=policy,
        user_id=pubkey,
        chat_id=channel_id,
        message_text=text,
    )

    if text.strip().lower() == "/new":
        if not result:
            audit_log_inbound_message(
                platform=MessagingPlatform.BUZZ.value,
                user_id=pubkey,
                chat_id=channel_id,
                message_hash=message_hash(text),
                authorized=False,
                reason=result.reason,
            )
            return InboundDecision(allowed=False, reply_text=result.reason)
        audit_log_inbound_message(
            platform=MessagingPlatform.BUZZ.value,
            user_id=pubkey,
            chat_id=channel_id,
            message_hash=message_hash(text),
            authorized=True,
            reason="session rotate",
        )
        return InboundDecision(allowed=True, reply_text=ROTATE_SESSION)

    audit_log_inbound_message(
        platform=MessagingPlatform.BUZZ.value,
        user_id=pubkey,
        chat_id=channel_id,
        message_hash=message_hash(text),
        authorized=bool(result),
        reason=result.reason,
    )
    if result:
        return InboundDecision(allowed=True)
    return InboundDecision(allowed=False, reply_text=result.reason)


def is_pubkey_authorized(*, pubkey: str, channel_id: str, env_allowed_pubkeys: list[str]) -> bool:
    """Whether *pubkey* is an authorized Buzz identity right now.

    Gates approval-reply resolution the same way a regular inbound message is
    gated: a participant who is not on the identity allowlist must not be
    able to approve or deny a pending write-tool action just by replying to
    its prompt message.
    """
    _, policy = load_identity_policy(MessagingPlatform.BUZZ.value)
    if env_allowed_pubkeys and not policy.allowed_user_ids:
        policy.allowed_user_ids = list(env_allowed_pubkeys)
        policy.inbound_enabled = True
    result = authorize_inbound_message(policy=policy, user_id=pubkey, chat_id=channel_id)
    audit_log_inbound_message(
        platform=MessagingPlatform.BUZZ.value,
        user_id=pubkey,
        chat_id=channel_id,
        authorized=bool(result),
        reason=f"approval-reply: {result.reason}",
    )
    return bool(result)


__all__ = [
    "InboundDecision",
    "enforce_inbound_buzz_message_security",
    "is_pubkey_authorized",
]
