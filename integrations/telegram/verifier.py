"""Telegram integration verifier — Bot API getMe probe."""

from __future__ import annotations

from typing import Any

import requests

from infrastructure.delivery.notifications.redaction import redact_token
from integrations.verification import register_verifier, result


@register_verifier("telegram")
def verify_telegram(source: str, config: dict[str, Any]) -> dict[str, str]:
    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return result("telegram", source, "missing", "Missing bot_token.")

    try:
        response = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        # requests embeds the full URL — which contains the bot token — in
        # HTTPError/ConnectionError messages (e.g. "401 ... for url:
        # https://api.telegram.org/bot<token>/getMe"), so the token must be
        # redacted before the detail reaches the verify output.
        safe_error = redact_token(str(exc), bot_token)
        return result("telegram", source, "failed", f"Telegram API check failed: {safe_error}")

    if not payload.get("ok"):
        description = redact_token(str(payload.get("description", "unknown error")), bot_token)
        return result(
            "telegram",
            source,
            "failed",
            f"Telegram API check failed: {description}",
        )

    user = payload.get("result", {})
    username = str(user.get("username", "")).strip()
    return result(
        "telegram",
        source,
        "passed",
        f"Connected to Telegram bot @{username or 'unknown'}.",
    )
