"""Discord integration verifier — bot-token probe against ``/users/@me``.

A single GET with the bot token proves the token is valid, and it needs no
third-party client. The previous implementation logged in with ``discord.py``
(an optional dependency that is usually absent, and whose blocking ``client.run``
does not belong in a setup probe), so it reported every token as "discord.py is
not installed". This is now the one prober both the CLI and the onboarding
wizard use.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.discord import DISCORD_API_BASE
from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_verifier, result

logger = logging.getLogger(__name__)

_ME_URL = f"{DISCORD_API_BASE}/users/@me"


@register_verifier("discord")
def verify_discord(source: str, config: dict[str, Any]) -> dict[str, str]:
    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("discord", source, "missing", "Missing bot_token.")

    try:
        response = httpx.get(_ME_URL, headers={"Authorization": f"Bot {bot_token}"}, timeout=10)
    except httpx.HTTPError as exc:
        safe_error = redact_token(str(exc), bot_token)
        return result("discord", source, "failed", f"Discord API check failed: {safe_error}")

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        return result("discord", source, "failed", "Discord bot token is invalid or revoked.")
    if response.status_code != HTTPStatus.OK:
        return result(
            "discord", source, "failed", f"Discord API check failed: HTTP {response.status_code}."
        )

    try:
        username = str(response.json().get("username", "")).strip()
    except Exception:
        logger.debug("Failed to parse Discord /users/@me response", exc_info=True)
        username = ""
    return result(
        "discord", source, "passed", f"Discord bot authenticated as @{username or 'unknown'}."
    )
