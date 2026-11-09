"""Resolve a Telegram chat reference to the numeric id delivery actually uses.

Telegram's ``sendMessage`` accepts either a numeric ``chat_id`` or an
``@username`` — but the username form only works for **public** channels and
supergroups; private groups and direct messages have no username at all. That
asymmetry is a setup-time trap: ``@my_alerts`` is accepted here and rejected at
the first real alert, hours later.

``getChat`` removes the trap. It accepts both forms, so setup can take whichever
the user has, and it answers the question ``getMe`` cannot: *can this bot see
that chat at all?* Resolving to the numeric id and storing that also decouples
delivery from the username, which a channel owner can change at any time.

One limit worth being explicit about: reaching a chat is not the same as being
allowed to post in it. A bot must be a channel administrator to send, and
``getChat`` succeeds without that. Only an actual send proves posting rights.
"""

from __future__ import annotations

from typing import Any

import requests

from infrastructure.delivery.notifications.redaction import redact_token

_GET_CHAT_TIMEOUT_SECONDS = 10


def _describe(chat: dict[str, Any]) -> str:
    """Render a chat as ``Acme Alerts (channel)`` for confirmation output."""
    title = str(chat.get("title") or chat.get("username") or "").strip()
    # Private chats carry no title; fall back to the account's own name.
    if not title:
        first = str(chat.get("first_name") or "").strip()
        last = str(chat.get("last_name") or "").strip()
        title = " ".join(part for part in (first, last) if part)
    chat_type = str(chat.get("type") or "chat").strip()
    return f"{title} ({chat_type})" if title else chat_type


def resolve_chat_id(*, bot_token: str, chat_id: str) -> tuple[str, str, str]:
    """Resolve *chat_id* via ``getChat``.

    Returns ``(numeric_id, description, error)``. On failure the first two are
    empty and *error* explains why, already stripped of the bot token — the
    token is embedded in the request URL and therefore in ``requests``' error
    messages.
    """
    reference = chat_id.strip()
    if not reference:
        return "", "", "Missing chat id."

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getChat",
            params={"chat_id": reference},
            timeout=_GET_CHAT_TIMEOUT_SECONDS,
        )
        payload = response.json()
    except Exception as exc:
        return "", "", f"Telegram chat lookup failed: {redact_token(str(exc), bot_token)}"

    if not payload.get("ok"):
        description = str(payload.get("description", "unknown error"))
        return "", "", f"Telegram cannot reach chat {reference}: {description}"

    chat = payload.get("result", {})
    resolved = str(chat.get("id", "")).strip()
    if not resolved:
        return "", "", f"Telegram returned no id for chat {reference}."
    return resolved, _describe(chat), ""


__all__ = ["resolve_chat_id"]
