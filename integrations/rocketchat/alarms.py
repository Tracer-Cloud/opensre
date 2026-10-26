"""Rocket.Chat alarm dispatcher with per-key cooldown.

Provides reusable throttled Rocket.Chat delivery and mirrors
:class:`integrations.telegram.alarms.AlarmDispatcher`.

Credential resolution lives in :mod:`integrations.rocketchat.credentials`;
raw transport in :mod:`integrations.rocketchat.delivery`. This module owns
only the throttling + dispatch policy.
"""

from __future__ import annotations

import logging
import time

from infrastructure.delivery.notifications.cooldown import CooldownGate
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate
from integrations.rocketchat.credentials import RocketChatCredentials
from integrations.rocketchat.delivery import post_rocketchat_message, post_rocketchat_webhook

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 300.0
_ROCKETCHAT_MESSAGE_LIMIT = MAX_MESSAGE_SIZE


class RocketChatAlarmDispatcher:
    """Dispatch Rocket.Chat alarms with per-key cooldown.

    Token credentials with a channel are preferred; the incoming webhook
    (fixed destination) is used only when token credentials are absent — the
    routing rule already established for ``rocketchat_send_message``, the
    cron provider, and the background-notify channel.
    """

    def __init__(
        self,
        creds: RocketChatCredentials,
        *,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._creds = creds
        self._gate = CooldownGate(cooldown_seconds)

    def dispatch(self, threshold_name: str, message: str) -> bool:
        """Send to Rocket.Chat unless this threshold is in cooldown."""
        now = self._now()

        remaining = self._gate.try_reserve(threshold_name, now)
        if remaining is not None:
            logger.debug(
                "alarm suppressed by cooldown: name=%s remaining=%.1fs",
                threshold_name,
                remaining,
            )
            return False

        text = truncate(message, _ROCKETCHAT_MESSAGE_LIMIT, suffix="…")

        # The cooldown slot was reserved before this network call. If the
        # delivery returns ok=False OR raises, the slot stays armed for the
        # cooldown window and the next caller for the same key is silently
        # suppressed — emit the same warning in both paths so operators see
        # the original failure instead of only the suppression debug line.
        try:
            if self._creds.has_token:
                ok, error, _message_id = post_rocketchat_message(
                    self._creds.server_url,
                    self._creds.channel,
                    text,
                    self._creds.auth_token,
                    self._creds.user_id,
                )
            else:
                ok, error = post_rocketchat_webhook(self._creds.webhook_url, text)
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
