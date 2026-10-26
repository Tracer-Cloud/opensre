"""Buzz delivery helper - posts messages via ``buzz-cli``."""

from __future__ import annotations

import logging

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.buzz.client import BuzzClient
from integrations.config_models import BuzzConfig

logger = logging.getLogger(__name__)


def post_buzz_message(
    relay_url: str,
    channel: str,
    text: str,
    private_key: str,
    *,
    auth_tag: str = "",
    buzz_path: str = "buzz",
    reply_to: str = "",
) -> tuple[bool, str, str]:
    """Send a message via ``buzz messages send``.

    Returns ``(ok, error, event_id)``. ``ok`` is False on expected failures
    (missing binary, relay unreachable, auth rejected, ...).
    """
    config = BuzzConfig(
        relay_url=relay_url,
        private_key=private_key,
        auth_tag=auth_tag,
        buzz_path=buzz_path or "buzz",
    )
    result = BuzzClient(config).send_message(channel=channel, content=text, reply_to=reply_to)
    if not result["success"]:
        safe_error = redact_token(str(result["error"]), private_key)
        logger.warning("[buzz] send message failed: %s", safe_error)
        return False, safe_error, ""
    return True, "", str(result["event_id"])


__all__ = ["post_buzz_message"]
