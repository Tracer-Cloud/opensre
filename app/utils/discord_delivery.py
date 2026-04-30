"""Discord delivery helper - posts investigation findings to Discord API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.utils.delivery_transport import post_json
from app.utils.truncation import truncate

logger = logging.getLogger(__name__)


def _discord_auth_headers(bot_token: str) -> dict[str, str]:
    # ``Content-Type: application/json`` is set automatically by httpx when
    # the request uses the ``json=`` kwarg, so we only need to add auth.
    return {"Authorization": f"Bot {bot_token}"}


def _redact_token(text: str, bot_token: str) -> str:
    """Replace ``bot_token`` with ``<redacted>`` to prevent accidental log/error leakage."""
    if bot_token and bot_token in text:
        return text.replace(bot_token, "<redacted>")
    return text


def _extract_error(data: Mapping[str, Any], status_code: int, text: str) -> str:
    """Return a human-readable error string from a Discord API response.

    Tries ``data["message"]`` / ``data["error"]`` first, then falls back to
    the raw response body or the HTTP status code so non-JSON failure bodies
    (HTML, plain text) never cause a crash.
    """
    msg = data.get("message")
    if msg:
        return str(msg)
    err = data.get("error")
    if err:
        return str(err)
    if text:
        return text[:500]
    return f"HTTP {status_code}"


def post_discord_message(
    channel_id: str,
    embeds: list[dict[str, Any]],
    bot_token: str,
    content: str = "",
) -> tuple[bool, str, str]:
    """Call discord channels api to post message on channel.

    Returns True on success, False on expected failures.
    """
    logger.debug("[discord] post message params channel_id: %s", channel_id)
    response = post_json(
        url=f"https://discord.com/api/v10/channels/{channel_id}/messages",
        payload={"content": content, "embeds": embeds},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        safe_error = _redact_token(response.error, bot_token)
        logger.warning("[discord] post message exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code not in (200, 201):
        logger.warning("[discord] post message failed: %s", response.status_code)
        error_message = _extract_error(response.data, response.status_code, response.text)
        safe_error = _redact_token(error_message, bot_token)
        logger.warning("[discord] post message failed: %s", safe_error)
        return False, safe_error, ""
    message_id = str(response.data.get("id") or "")
    return True, "", message_id


def create_discord_thread(
    channel_id: str,
    message_id: str,
    name: str,
    bot_token: str,
) -> tuple[bool, str, str]:
    """Call discord channels api to create a thread.

    Returns True on success, False on expected failures.
    """
    response = post_json(
        url=f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads",
        payload={"name": name, "auto_archive_duration": 1440},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        safe_error = _redact_token(response.error, bot_token)
        logger.warning("[discord] create thread exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code not in (200, 201):
        error_message = _extract_error(response.data, response.status_code, response.text)
        safe_error = _redact_token(error_message, bot_token)
        logger.warning("[discord] create thread failed: %s", safe_error)
        return False, safe_error, ""
    thread_id = str(response.data.get("id") or "")
    return True, "", thread_id


_EMBED_TITLE_LIMIT = 256
_EMBED_DESCRIPTION_LIMIT = 4096


def send_discord_report(report: str, discord_ctx: dict[str, Any]) -> tuple[bool, str]:
    channel_id: str = str(discord_ctx.get("channel_id") or "")
    thread_id: str = str(discord_ctx.get("thread_id") or "")
    bot_token: str = str(discord_ctx.get("bot_token") or "")
    embed = {
        "title": truncate("Investigation Complete", _EMBED_TITLE_LIMIT, suffix="…"),
        "color": 15158332,
        "description": truncate(report, _EMBED_DESCRIPTION_LIMIT, suffix="…"),
        "footer": {"text": "OpenSRE Investigation"},
    }
    target = thread_id if thread_id else channel_id
    post_message_success, error, _ = post_discord_message(target, [embed], bot_token)
    return (True, "") if post_message_success else (False, error)
