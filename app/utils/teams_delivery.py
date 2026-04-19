"""Microsoft Teams delivery helper - posts to Teams Incoming Webhooks."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.output import debug_print

logger = logging.getLogger(__name__)


def send_ms_teams_report(
    message: str,
    webhook_url: str | None = None,
    **extra: Any,
) -> tuple[bool, str]:
    """
    Post the RCA report to Microsoft Teams via Incoming Webhook.

    Args:
        message: The formatted RCA report text.
        webhook_url: Optional webhook URL. Falls back to TEAMS_WEBHOOK_URL env var.
        **extra: Any additional params merged into the payload.

    Returns:
        (success, error_detail) — success is True if posted, error_detail is non-empty on failure.
    """
    target_url = webhook_url or os.getenv("TEAMS_WEBHOOK_URL", "").strip()

    if not target_url:
        logger.debug("[teams] Delivery skipped: no webhook_url")
        debug_print("Microsoft Teams delivery skipped: no TEAMS_WEBHOOK_URL configured.")
        return False, "no_webhook_url"

    success = _post_via_ms_teams_webhook(message, target_url, **extra)
    return (True, "") if success else (False, "webhook_failed")


def _post_via_ms_teams_webhook(
    text: str,
    webhook_url: str,
    **extra: Any,
) -> bool:
    # Modern Teams Workflows require an Adaptive Card payload.
    # Legacy 'text' payloads are being retired.
    payload: dict[str, Any] = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.2",
                    "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                },
            }
        ],
    }
    if extra:
        # For Workflows, extra fields are typically ignored or must be mapped
        # into the body, but we keep the update for compatibility.
        payload.update(extra)

    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        debug_print(
            f"Microsoft Teams incoming webhook failed: HTTP {exc.response.status_code if exc.response else 'unknown'}: {detail[:200]}"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        debug_print(f"Microsoft Teams incoming webhook failed: {exc}")
        return False
    else:
        debug_print("Microsoft Teams report posted via incoming webhook.")
        return True
