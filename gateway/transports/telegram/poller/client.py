"""Telegram Bot API client (httpx, outbound + gateway edits)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from infrastructure.delivery.notifications.delivery_transport import post_json
from infrastructure.delivery.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramBotClient:
    """Minimal Telegram Bot API wrapper for gateway operations."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token

    def _call(self, method: str, payload: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
        response = post_json(
            url=_API_BASE.format(token=self._token, method=method),
            payload=payload,
        )
        if not response.ok:
            return False, {}, redact_token(response.error, self._token)
        if response.status_code != HTTPStatus.OK or not isinstance(response.data, Mapping):
            error = response.text or f"HTTP {response.status_code}"
            return False, {}, redact_token(error, self._token)
        if not response.data.get("ok"):
            description = redact_token(
                str(response.data.get("description", "unknown")), self._token
            )
            return False, dict(response.data), description
        result = response.data.get("result")
        return True, dict(result) if isinstance(result, Mapping) else {}, ""

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)
        ok, result, error = self._call("sendMessage", payload)
        if not ok:
            logger.warning("[telegram-gateway] sendMessage failed: %s", error)
            return False, error, ""
        return True, "", str(result.get("message_id") or "")

    def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)
        ok, _, error = self._call("editMessageText", payload)
        if not ok:
            logger.debug("[telegram-gateway] editMessageText failed: %s", error)
        return ok, error

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        ok, _, error = self._call("answerCallbackQuery", payload)
        if not ok:
            logger.debug("[telegram-gateway] answerCallbackQuery failed: %s", error)

    def send_chat_action(self, chat_id: str, action: str = "typing") -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {})
