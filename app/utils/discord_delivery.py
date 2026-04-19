"""Discord delivery helper - posts investigation findings to Discord API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

def post_discord_message(channel_id: str, embeds:list[dict[str, Any]], bot_token:str, content: str = "") -> tuple[bool, str, str] :
    """Call discord channels api to post message on channel.

    Returns True on success, False on expected failures.
    """
    logger.debug("[discord] post message params channel_id: %s", channel_id)
    try:
        resp = httpx.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            json={"content": content, "embeds": embeds},
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15.0,
        )
        data = resp.json()
        error_message = ""
        if resp.status_code not in (200, 201):
            logger.warning("[discord] post message failed: %s", resp.status_code)
            logger.warning("[discord] api response %s", data)
            error_message = data.get("message", data.get("error", "unknown"))
            logger.warning("[discord] post message failed: %s", error_message)
            return (False, error_message, "")
        message_id: str = str(data.get("id") or "")
        return (True, error_message, message_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discord] post message exception: %s", exc)
        return (False, str(exc), "")


def create_discord_thread(channel_id: str, message_id: str, name:str, bot_token:str) -> tuple[bool, str, str] :
    """Call discord channels api to create a thread.

    Returns True on success, False on expected failures.
    """
    try:
        resp = httpx.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads",
            json={"name": name, "auto_archive_duration": 1440},
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15.0,
        )
        data = resp.json()
        error_message = ""
        if resp.status_code not in (200, 201):
            error_message = data.get("message", data.get("error", "unknown"))
            logger.warning("[discord] create thread failed: %s", error_message)
            return (False, error_message, "")
        thread_id: str = str(data.get("id") or "")
        return (True, error_message, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[discord] create thread exception: %s", exc)
        return (False, str(exc), "")


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
