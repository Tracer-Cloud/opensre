"""Telegram alarm dispatcher with per-key cooldown.

Provides reusable throttled Telegram delivery. The dispatcher takes a string
key and suppresses repeat deliveries for that key within the cooldown window.

Credential resolution lives in
:mod:`integrations.telegram.credentials`; raw transport in
:mod:`integrations.telegram.delivery`. This module owns only the
throttling + dispatch policy.
"""

from __future__ import annotations

import logging
import time

from infrastructure.delivery.notifications.cooldown import CooldownGate
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.telegram.credentials import TelegramCredentials
from integrations.telegram.delivery import (
    post_telegram_message,
    truncate_for_telegram_html,
)

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_TELEGRAM_MESSAGE_LIMIT = MAX_MESSAGE_SIZE


class AlarmDispatcher:
    """Dispatch Telegram alarms with per-key cooldown."""

    def __init__(
        self,
        creds: TelegramCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        parse_mode: str = "",
    ) -> None:
        self._creds = creds
        self._parse_mode = parse_mode
        self._gate = CooldownGate(cooldown_seconds)

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Telegram unless this threshold is in cooldown."""
        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        if self._parse_mode.upper() == "HTML":
            text = truncate_for_telegram_html(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")
        else:
            text = truncate(message, _TELEGRAM_MESSAGE_LIMIT, suffix="…")

        # The cooldown slot was reserved before this network call (see lock
        # block above). If ``post_telegram_message`` returns ``ok=False`` OR
        # raises, the slot stays armed for the cooldown window and the next
        # caller for the same key is silently suppressed — emit the same
        # warning in both paths so operators see the original failure
        # instead of only the suppression debug line.
        try:
            ok, error, _ = post_telegram_message(
                chat_id=self._creds.chat_id,
                text=text,
                bot_token=self._creds.bot_token,
                parse_mode=self._parse_mode,
            )
        except Exception as exc:
            logger.warning(
                "alarm delivery raised and cooldown remains armed: name=%s error=%s",
                threshold_name,
                exc,
                exc_info=True,
            )
            return False

        if ok:
            return True

        logger.warning(
            "alarm delivery failed and cooldown remains armed: name=%s error=%s",
            threshold_name,
            error,
        )
        return False

    @staticmethod
    def _now() -> float:
        return time.monotonic()
