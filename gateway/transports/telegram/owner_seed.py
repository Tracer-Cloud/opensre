"""Seed the Telegram allowlist with the connection's private chat, once."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from gateway.core.middleware.identity_policy import load_identity_policy, save_identity_policy
from integrations.messaging_security import MessagingPlatform

logger = logging.getLogger(__name__)


def private_chat_user_id(credentials: Mapping[str, Any]) -> str:
    """Return ``default_chat_id`` when it is a private chat, else ``""``.

    A private chat's ID equals the user's ``from.id`` and is positive; group and
    channel IDs are negative and are never treated as a user.
    """
    chat_id = str(credentials.get("default_chat_id") or "").strip()
    return chat_id if chat_id.isdigit() and int(chat_id) > 0 else ""


def seed_owner_from_private_chat(*, env_allowed_user_ids: list[str]) -> None:
    """Add the connection's private chat to the stored allowlist at most once.

    Skipped when ``TELEGRAM_ALLOWED_USERS`` is set. Once ``owner_seeded`` is
    stored, later revocations stand. A store failure is logged, not raised.
    """
    if env_allowed_user_ids:
        return
    try:
        record, policy = load_identity_policy(MessagingPlatform.TELEGRAM.value)
        if record is None or policy.owner_seeded:
            return
        owner = private_chat_user_id(record.get("credentials") or {})
        if not owner:
            return
        if not policy.allowed_user_ids:
            policy.allowed_user_ids = [owner]
            policy.inbound_enabled = True
        policy.owner_seeded = True
        save_identity_policy(MessagingPlatform.TELEGRAM.value, record, policy)
    except Exception:
        logger.exception("[telegram-gateway] could not seed the owner allowlist")


__all__ = ["private_chat_user_id", "seed_owner_from_private_chat"]
