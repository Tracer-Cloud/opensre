"""Discord delivery helper - posts investigation findings to Discord API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.utils.delivery_transport import post_json

logger = logging.getLogger(__name__)


def _discord_auth_headers(bot_token: str) -> dict[str, str]:
    # ``Content-Type: application/json`` is set automatically by httpx when
    # the request uses the ``json=`` kwarg, so we only need to add auth.
    return {"Authorization": f"Bot {bot_token}"}


def _discord_error_from_data(data: Mapping[str, Any]) -> str:
    return str(data.get("message", data.get("error", "unknown")))


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
        logger.warning("[discord] post message exception: %s", response.error)
        return False, response.error, ""
    if response.status_code not in (200, 201):
        logger.warning("[discord] post message failed: %s", response.status_code)
        logger.warning("[discord] api response %s", response.data)
        error_message = _discord_error_from_data(response.data)
        logger.warning("[discord] post message failed: %s", error_message)
        return False, error_message, ""
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
        logger.warning("[discord] create thread exception: %s", response.error)
        return False, response.error, ""
    if response.status_code not in (200, 201):
        error_message = _discord_error_from_data(response.data)
        logger.warning("[discord] create thread failed: %s", error_message)
        return False, error_message, ""
    thread_id = str(response.data.get("id") or "")
    return True, "", thread_id


_EMBED_TITLE_LIMIT = 256
_EMBED_DESCRIPTION_LIMIT = 4096


def _truncate(text: str, limit: int) -> str:
    return (text[: limit - 1] + "…") if len(text) > limit else text


def send_discord_report(report: str, discord_ctx: dict[str, Any]) -> tuple[bool, str]:
    channel_id: str = str(discord_ctx.get("channel_id") or "")
    thread_id: str = str(discord_ctx.get("thread_id") or "")
    bot_token: str = str(discord_ctx.get("bot_token") or "")
    embed = {
        "title": _truncate("Investigation Complete", _EMBED_TITLE_LIMIT),
        "color": 15158332,
        "description": _truncate(report, _EMBED_DESCRIPTION_LIMIT),
        "footer": {"text": "OpenSRE Investigation"},
    }
    target = thread_id if thread_id else channel_id
    post_message_success, error, _ = post_discord_message(target, [embed], bot_token)
    return (True, "") if post_message_success else (False, error)
