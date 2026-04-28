from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from app.utils.delivery_transport import post_json

logger = logging.getLogger(__name__)

_DISCORD_TOKEN_RE = re.compile(r"([a-zA-Z0-9_-]{24,}\.[a-zA-Z0-9_-]{6,}\.[a-zA-Z0-9_-]{27,})")


def _redact_arg(a: object) -> object:
    """Redact Discord token from a log arg, preserving the original type if no match."""
    s = str(a)
    redacted = _DISCORD_TOKEN_RE.sub("<redacted>", s)
    return redacted if redacted != s else a


class _DiscordTokenFilter(logging.Filter):
    """Scrub Discord tokens from httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _DISCORD_TOKEN_RE.sub("<redacted>", str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def _install_httpx_token_filter() -> None:
    for name in ("httpx", "httpcore"):
        lgr = logging.getLogger(name)
        # Avoid adding multiple instances of the same filter type
        if not any(isinstance(f, _DiscordTokenFilter) for f in lgr.filters):
            lgr.addFilter(_DiscordTokenFilter())


_install_httpx_token_filter()


def _redact_token(text: str, token: str) -> str:
    """Replace token with <redacted> to prevent accidental log/error leakage."""
    if token and token in text:
        return text.replace(token, "<redacted>")
    return text


def _discord_auth_headers(bot_token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {bot_token}"}


def _discord_error_from_data(data: Any, bot_token: str) -> str:
    if not isinstance(data, Mapping):
        return "unknown"
    err = str(data.get("message", data.get("error", "unknown")))
    return _redact_token(err, bot_token)


def post_discord_message(
    channel_id: str,
    embeds: list[dict[str, Any]],
    bot_token: str,
    content: str = "",
) -> tuple[bool, str, str]:
    """Call discord channels api to post message on channel.

    Returns (success, error, message_id).
    """
    logger.debug("[discord] post message params channel_id: %s", channel_id)
    response = post_json(
        url=f"https://discord.com/api/v10/channels/{channel_id}/messages",
        payload={"content": content, "embeds": embeds},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        error = _redact_token(response.error, bot_token)
        logger.warning("[discord] post message exception: %s", error)
        return False, error, ""

    if response.status_code not in (200, 201):
        logger.warning("[discord] post message failed: %s", response.status_code)
        logger.warning(
            "[discord] api response %s", _redact_token(response.text or "empty", bot_token)
        )
        error_message = _discord_error_from_data(response.data, bot_token)
        logger.warning("[discord] post message failed: %s", error_message)
        return False, error_message, ""

    message_id: str = str(response.data.get("id") or "")
    return True, "", message_id


def create_discord_thread(
    channel_id: str,
    message_id: str,
    name: str,
    bot_token: str,
) -> tuple[bool, str, str]:
    """Call discord channels api to create a thread.

    Returns (success, error, thread_id).
    """
    response = post_json(
        url=f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads",
        payload={"name": name, "auto_archive_duration": 1440},
        headers=_discord_auth_headers(bot_token),
    )
    if not response.ok:
        error = _redact_token(response.error, bot_token)
        logger.warning("[discord] create thread exception: %s", error)
        return False, error, ""

    if response.status_code not in (200, 201):
        error_message = _discord_error_from_data(response.data, bot_token)
        logger.warning("[discord] create thread failed: %s", error_message)
        return False, error_message, ""

    thread_id: str = str(response.data.get("id") or "")
    return True, "", thread_id


_EMBED_TITLE_LIMIT = 256
_EMBED_DESCRIPTION_LIMIT = 4096


def _truncate(text: str, limit: int) -> str:
    return (text[: limit - 1] + "…") if len(text) > limit else text


def send_discord_report(report: str, discord_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Send investigation report to Discord."""
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

