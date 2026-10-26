"""Long-polling transport for local Telegram gateway development."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from gateway.transports.telegram.poller.parse_telegram_update import (
    parse_callback_query,
    parse_update,
)
from gateway.transports.telegram.settings import (
    TelegramCallbackQuery,
    TelegramInboundMessage,
)
from infrastructure.delivery.notifications.redaction import redact_token

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/getUpdates"
_CONFLICT_ERROR_CODE = 409
_DEFAULT_RETRY_SECONDS = 2.0
_MAX_CONFLICT_BACKOFF_SECONDS = 30.0
_WARNING_COOLDOWN_SECONDS = 60.0


@dataclass(slots=True)
class TelegramPollResult:
    """One getUpdates batch split into DM messages and callback clicks."""

    messages: list[TelegramInboundMessage] = field(default_factory=list)
    callbacks: list[TelegramCallbackQuery] = field(default_factory=list)


def _decode_telegram_response(response: httpx.Response) -> dict[str, Any]:
    """Parse a Telegram Bot API JSON body regardless of HTTP status."""
    try:
        data = response.json()
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class TelegramPoller:
    """Poll Telegram getUpdates and yield normalized inbound events."""

    def __init__(self, bot_token: str, *, timeout: int = 30) -> None:
        self._token = bot_token
        self._timeout = timeout
        self._offset = 0
        self._conflict_backoff_seconds = _DEFAULT_RETRY_SECONDS
        self._last_warning_monotonic = 0.0

    def poll_once(self) -> TelegramPollResult:
        url = _API.format(token=self._token)
        params: dict[str, str | int | list[str]] = {
            "timeout": self._timeout,
            "offset": self._offset + 1,
            "allowed_updates": ["message", "callback_query"],
        }
        try:
            response = httpx.get(url, params=params, timeout=float(self._timeout + 5))
        except Exception as exc:
            safe_error = redact_token(str(exc), self._token)
            self._log_transient(
                "[telegram-gateway] getUpdates failed (%s): %s",
                type(exc).__name__,
                safe_error,
            )
            time.sleep(_DEFAULT_RETRY_SECONDS)
            return TelegramPollResult()

        data = _decode_telegram_response(response)
        if not data.get("ok"):
            self._handle_poll_error(data=data, response=response)
            return TelegramPollResult()

        self._conflict_backoff_seconds = _DEFAULT_RETRY_SECONDS
        result = data.get("result")
        if not isinstance(result, list):
            return TelegramPollResult()

        messages: list[TelegramInboundMessage] = []
        callbacks: list[TelegramCallbackQuery] = []
        for raw in result:
            if not isinstance(raw, dict):
                continue
            update_id = int(raw.get("update_id") or 0)
            self._offset = max(self._offset, update_id)
            parsed = parse_update(raw)
            if parsed is not None:
                messages.append(parsed)
                continue
            callback = parse_callback_query(raw)
            if callback is not None:
                callbacks.append(callback)
        return TelegramPollResult(messages=messages, callbacks=callbacks)

    def _handle_poll_error(
        self,
        *,
        data: dict[str, Any],
        response: httpx.Response,
    ) -> None:
        error_code = data.get("error_code")
        description = redact_token(
            str(data.get("description") or response.text.strip() or f"HTTP {response.status_code}"),
            self._token,
        )
        if error_code == _CONFLICT_ERROR_CODE:
            logger.debug(
                "[telegram-gateway] getUpdates conflict (retry in %.0fs): %s",
                self._conflict_backoff_seconds,
                description,
            )
            time.sleep(self._conflict_backoff_seconds)
            self._conflict_backoff_seconds = min(
                self._conflict_backoff_seconds * 2,
                _MAX_CONFLICT_BACKOFF_SECONDS,
            )
            return

        self._log_transient(
            "[telegram-gateway] getUpdates not ok (HTTP %s, error_code=%s): %s",
            response.status_code,
            error_code,
            description,
        )
        time.sleep(_DEFAULT_RETRY_SECONDS)

    def _log_transient(self, message: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_warning_monotonic < _WARNING_COOLDOWN_SECONDS:
            logger.debug(message, *args)
            return
        self._last_warning_monotonic = now
        logger.warning(message, *args)
