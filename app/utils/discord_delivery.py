"""Discord delivery helper - posts investigation findings to Discord API."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

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
    _filter = _DiscordTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_filter)


_install_httpx_token_filter()


def _redact_token(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "<redacted>")
    return text


def _response_error_message(resp: Any) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return str(getattr(resp, "text", "")) or f"HTTP {getattr(resp, 'status_code', 'unknown')}"

    if isinstance(data, dict):
        for key in ("message", "error", "detail", "description"):
            value = data.get(key)
            if value:
                return str(value)
        return "unknown"

    return str(data) if data else f"HTTP {getattr(resp, 'status_code', 'unknown')}"


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
    try:
        resp = httpx.post(
            url=f"https://discord.com/api/v10/channels/{channel_id}/messages",
            json={"content": content, "embeds": embeds},
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15.0,
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            error_message = _redact_token(_response_error_message(resp), bot_token)
            logger.warning("[discord] post message failed: %s", resp.status_code)
            logger.warning("[discord] post message failed: %s", error_message)
            return False, error_message, ""
        if not isinstance(data, dict):
            error_message = _redact_token(_response_error_message(resp), bot_token)
            logger.warning("[discord] post message failed: %s", resp.status_code)
            logger.warning("[discord] post message failed: %s", error_message)
            return False, error_message, ""
        if resp.status_code not in (200, 201):
            logger.warning("[discord] post message failed: %s", resp.status_code)
            logger.warning("[discord] api response %s", _redact_token(str(data), bot_token))
            error_message = _redact_token(str(data.get("message", data.get("error", "unknown"))), bot_token)
            logger.warning("[discord] post message failed: %s", error_message)
            return False, error_message, ""
        message_id: str = str(data.get("id") or "")
        return True, "", message_id
    except Exception as exc:  # noqa: BLE001
        error = _redact_token(str(exc), bot_token)
        logger.warning("[discord] post message exception: %s", error)
        return False, error, ""


def create_discord_thread(
    channel_id: str,
    message_id: str,
    name: str,
    bot_token: str,
) -> tuple[bool, str, str]:
    """Call discord channels api to create a thread.

    Returns True on success, False on expected failures.
    """
    try:
        resp = httpx.post(
            url=(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/threads"
            ),
            json={"name": name, "auto_archive_duration": 1440},
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15.0,
        )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            error_message = _redact_token(_response_error_message(resp), bot_token)
            logger.warning("[discord] create thread failed: %s", error_message)
            return False, error_message, ""
        if not isinstance(data, dict):
            error_message = _redact_token(_response_error_message(resp), bot_token)
            logger.warning("[discord] create thread failed: %s", error_message)
            return False, error_message, ""
        if resp.status_code not in (200, 201):
            error_message = _redact_token(str(data.get("message", data.get("error", "unknown"))), bot_token)
            logger.warning("[discord] create thread failed: %s", error_message)
            return False, error_message, ""
        thread_id: str = str(data.get("id") or "")
        return True, "", thread_id
    except Exception as exc:  # noqa: BLE001
        error = _redact_token(str(exc), bot_token)
        logger.warning("[discord] create thread exception: %s", error)
        return False, error, ""


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
