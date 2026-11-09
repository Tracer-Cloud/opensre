"""Discord delivery helper - posts report messages to the Discord API."""

from __future__ import annotations

import logging
from typing import Any

from config.constants.discord import DISCORD_API_BASE
from infrastructure.delivery.notifications.delivery_errors import extract_http_error
from infrastructure.delivery.notifications.delivery_transport import post_json
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.delivery.notifications.redaction import redact_token
from infrastructure.text.truncation import truncate

logger = logging.getLogger(__name__)


def _discord_auth_headers(bot_token: str) -> dict[str, str]:
    # ``Content-Type: application/json`` is set automatically by httpx when
    # the request uses the ``json=`` kwarg, so we only need to add auth.
    return {"Authorization": f"Bot {bot_token}"}


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
        url=f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        payload={"content": content, "embeds": embeds},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        safe_error = redact_token(response.error, bot_token)
        logger.warning("[discord] post message exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code not in (200, 201):
        logger.warning("[discord] post message failed: %s", response.status_code)
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, bot_token)
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
        url=f"{DISCORD_API_BASE}/channels/{channel_id}/messages/{message_id}/threads",
        payload={"name": name, "auto_archive_duration": 1440},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        safe_error = redact_token(response.error, bot_token)
        logger.warning("[discord] create thread exception: %s", safe_error)
        return False, safe_error, ""
    if response.status_code not in (200, 201):
        error_message = extract_http_error(response.data, response.status_code, response.text)
        safe_error = redact_token(error_message, bot_token)
        logger.warning("[discord] create thread failed: %s", safe_error)
        return False, safe_error, ""
    thread_id = str(response.data.get("id") or "")
    return True, "", thread_id


_EMBED_TITLE_LIMIT = 256
_EMBED_DESCRIPTION_LIMIT = MAX_MESSAGE_SIZE


def send_discord_report(report: str, discord_ctx: dict[str, Any]) -> tuple[bool, str]:
    channel_id: str = str(discord_ctx.get("channel_id") or "")
    thread_id: str = str(discord_ctx.get("thread_id") or "")
    bot_token: str = str(discord_ctx.get("bot_token") or "")
    embed = {
        "title": truncate("OpenSRE Report", _EMBED_TITLE_LIMIT, suffix="…"),
        "color": 15158332,
        "description": truncate(report, _EMBED_DESCRIPTION_LIMIT, suffix="…"),
        "footer": {"text": "OpenSRE"},
    }
    target = thread_id if thread_id else channel_id
    post_message_success, error, _ = post_discord_message(target, [embed], bot_token)
    return (True, "") if post_message_success else (False, error)
