"""Read bot identity and recent chats without acknowledging updates or changing webhooks."""

from __future__ import annotations

from typing import Any

import requests


class TelegramSetupError(Exception):
    """A safe, actionable setup failure that contains no bot token."""


def _get(bot_token: str, method: str, **params: int) -> Any:
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{bot_token}/{method}",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise TelegramSetupError(
            "Telegram could not complete the check. Check your connection and bot token, then retry."
        ) from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise TelegramSetupError("Telegram rejected the check. Check the bot token and retry.")
    return payload.get("result")


def discover_bot(bot_token: str) -> str:
    """Return the authenticated bot's username."""
    bot = _get(bot_token, "getMe")
    if not isinstance(bot, dict) or not bot.get("username"):
        raise TelegramSetupError("Telegram returned no bot name. Try the check again.")
    return str(bot["username"])


def discover_chats(bot_token: str) -> list[tuple[str, str]]:
    """Offer at most 100 recent chats; never consume updates or disable an existing webhook."""
    webhook = _get(bot_token, "getWebhookInfo")
    if not isinstance(webhook, dict):
        raise TelegramSetupError("Telegram could not check this bot's connection. Retry the check.")
    if webhook.get("url"):
        raise TelegramSetupError(
            "This bot already delivers messages to another app. "
            "Use a public @channelname or an existing chat ID below; its connection will stay active."
        )
    updates = _get(bot_token, "getUpdates", limit=100, timeout=0)
    if not isinstance(updates, list):
        raise TelegramSetupError("Telegram could not list chats. Send a new message and retry.")
    chats: dict[str, str] = {}
    for update in updates[:100]:
        if not isinstance(update, dict):
            continue
        for key in ("message", "channel_post", "my_chat_member"):
            event = update.get(key)
            chat = event.get("chat") if isinstance(event, dict) else None
            if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
                continue
            chat_id = str(chat["id"])
            name = str(
                chat.get("title") or chat.get("first_name") or chat.get("username") or chat_id
            )
            chats[chat_id] = f"{name} ({chat.get('type', 'chat')}, {chat_id})"
    return list(chats.items())
