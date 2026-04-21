"""Telegram delivery helper - posts investigation findings to Telegram Bot API."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = 4096


def _truncate(text: str, limit: int) -> str:
    return (text[: limit - 1] + "…") if len(text) > limit else text


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
    if reply_to_message_id:
        with contextlib.suppress(ValueError, TypeError):
            payload["reply_to_message_id"] = int(reply_to_message_id)
    try:
        resp = httpx.post(
            url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=15.0,
        )
        data = resp.json()
        error_message = ""
        if resp.status_code not in (200, 201):
            logger.warning("[telegram] post message failed: %s", resp.status_code)
            logger.warning("[telegram] api response %s", data)
            error_message = str(data.get("description", data.get("error", "unknown")))
            logger.warning("[telegram] post message failed: %s", error_message)
            return False, error_message, ""
        result = data.get("result", {})
        message_id: str = str(result.get("message_id") or "")
        return True, error_message, message_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("[telegram] post message exception: %s", exc)
        return False, str(exc), ""


def send_telegram_report(report: str, telegram_ctx: dict[str, Any]) -> tuple[bool, str]:
    bot_token: str = str(telegram_ctx.get("bot_token") or "")
    chat_id: str = str(telegram_ctx.get("chat_id") or "")
    reply_to_message_id: str = str(telegram_ctx.get("reply_to_message_id") or "")
    text = _truncate(report, _MESSAGE_LIMIT)
    post_success, error, _ = post_telegram_message(
        chat_id, text, bot_token, reply_to_message_id=reply_to_message_id
    )
    return (True, "") if post_success else (False, error)
