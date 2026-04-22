"""Telegram delivery helper - posts investigation findings to Telegram Bot API."""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 4096
_BOT_TOKEN_RE = re.compile(r"(bot)[^/]+(/)")


def _redact_arg(a: object) -> object:
    """Redact bot token from a log arg, preserving the original type if no match."""
    s = str(a)
    redacted = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", s)
    return redacted if redacted != s else a


class _TelegramTokenFilter(logging.Filter):
    """Scrub Telegram bot tokens from httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def _install_httpx_token_filter() -> None:
    _filter = _TelegramTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_filter)


_install_httpx_token_filter()


def _truncate(text: str, limit: int) -> str:
    return (text[: limit - 1] + "…") if len(text) > limit else text


def _redact_token(text: str, bot_token: str) -> str:
    """Replace bot token with <redacted> to prevent accidental log/error leakage."""
    if bot_token and bot_token in text:
        return text.replace(bot_token, "<redacted>")
    return text


def post_telegram_message(
    chat_id: str,
    text: str,
    bot_token: str,
    parse_mode: str = "",
    reply_to_message_id: str = "",
) -> tuple[bool, str, str]:
    """Call Telegram Bot API sendMessage endpoint.

    Returns (success, error, message_id).
    """
    logger.debug("[telegram] post message params chat_id: %s", chat_id)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id and reply_to_message_id != "0":
        with contextlib.suppress(ValueError, TypeError):
            payload["reply_to_message_id"] = int(reply_to_message_id)
    try:
        resp = httpx.post(
            url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning("[telegram] post message failed: %s", resp.status_code)
            try:
                data = resp.json()
                error_message = str(data.get("description", data.get("error", "unknown")))
            except Exception:  # noqa: BLE001
                error_message = resp.text or f"HTTP {resp.status_code}"
            logger.warning("[telegram] post message failed: %s", error_message)
            return False, error_message, ""
        data = resp.json()
        result = data.get("result", {})
        message_id: str = str(result.get("message_id") or "")
        return True, "", message_id
    except Exception as exc:  # noqa: BLE001
        error = _redact_token(str(exc), bot_token)
        logger.warning("[telegram] post message exception: %s", error)
        return False, error, ""


def send_telegram_report(report: str, telegram_ctx: dict[str, Any]) -> tuple[bool, str]:
    """Send a truncated report to Telegram. Returns (success, error)."""
    bot_token: str = str(telegram_ctx.get("bot_token") or "")
    chat_id: str = str(telegram_ctx.get("chat_id") or "")
    if not bot_token or not chat_id:
        return False, "Missing bot_token or chat_id"
    reply_to_message_id: str = str(telegram_ctx.get("reply_to_message_id") or "")
    text = _truncate(report, _MESSAGE_LIMIT)
    post_success, error, _ = post_telegram_message(
        chat_id, text, bot_token, reply_to_message_id=reply_to_message_id
    )
    return (True, "") if post_success else (False, error)
