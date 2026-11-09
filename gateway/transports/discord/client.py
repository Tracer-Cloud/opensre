"""Discord REST helpers for posting turn replies."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from config.constants.discord import DISCORD_API_BASE

logger = logging.getLogger(__name__)

_MAX_CONTENT = 2000


def _auth_headers(bot_token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {bot_token}"}


def send_message(
    *,
    channel_id: str,
    content: str,
    bot_token: str,
) -> str | None:
    """Post a message; return message id or None."""
    payload: dict[str, Any] = {"content": content[:_MAX_CONTENT]}
    return _post_message(channel_id=channel_id, payload=payload, bot_token=bot_token)


def send_message_with_components(
    *,
    channel_id: str,
    content: str,
    components: list[dict[str, Any]],
    bot_token: str,
) -> str | None:
    """Post a message with action components; return message id or None."""
    payload: dict[str, Any] = {
        "content": content[:_MAX_CONTENT],
        "components": components,
    }
    return _post_message(channel_id=channel_id, payload=payload, bot_token=bot_token)


def _post_message(
    *,
    channel_id: str,
    payload: dict[str, Any],
    bot_token: str,
) -> str | None:
    try:
        response = httpx.post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            json=payload,
            headers=_auth_headers(bot_token),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("[discord-gateway] post failed: %s", type(exc).__name__)
        return None
    if response.status_code not in (
        HTTPStatus.OK,
        HTTPStatus.CREATED,
    ):
        logger.warning("[discord-gateway] post HTTP %s", response.status_code)
        return None
    return str(response.json().get("id") or "") or None


def edit_message_with_components(
    *,
    channel_id: str,
    message_id: str,
    content: str,
    components: list[dict[str, Any]] | None,
    bot_token: str,
) -> bool:
    """Edit message content and optional components."""
    payload: dict[str, Any] = {"content": content[:_MAX_CONTENT]}
    if components is not None:
        payload["components"] = components
    try:
        response = httpx.patch(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}",
            json=payload,
            headers=_auth_headers(bot_token),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("[discord-gateway] edit failed: %s", type(exc).__name__)
        return False
    return response.status_code == HTTPStatus.OK


def edit_message(
    *,
    channel_id: str,
    message_id: str,
    content: str,
    bot_token: str,
) -> bool:
    """Edit an existing message."""
    try:
        response = httpx.patch(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}",
            json={"content": content[:_MAX_CONTENT]},
            headers=_auth_headers(bot_token),
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("[discord-gateway] edit failed: %s", type(exc).__name__)
        return False
    return response.status_code == HTTPStatus.OK


def add_reaction(
    *,
    channel_id: str,
    message_id: str,
    emoji: str,
    bot_token: str,
) -> bool:
    """Add an emoji reaction to a message. ``emoji`` must be the Unicode emoji."""
    encoded = quote(emoji, safe="")
    try:
        response = httpx.put(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
            headers=_auth_headers(bot_token),
            timeout=10.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code in (
        HTTPStatus.OK,
        HTTPStatus.NO_CONTENT,
    )


def remove_reaction(
    *,
    channel_id: str,
    message_id: str,
    emoji: str,
    bot_token: str,
) -> bool:
    """Remove the bot's own emoji reaction. ``emoji`` must be the Unicode emoji."""
    encoded = quote(emoji, safe="")
    try:
        response = httpx.delete(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
            headers=_auth_headers(bot_token),
            timeout=10.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code in (
        HTTPStatus.OK,
        HTTPStatus.NO_CONTENT,
    )


def split_discord_content(text: str, *, limit: int = _MAX_CONTENT) -> list[str]:
    """Split long assistant text into Discord-sized chunks."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return parts
