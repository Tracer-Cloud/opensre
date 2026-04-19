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
    """Post a standalone RCA report via Microsoft Teams incoming webhook."""
    # Standard Incoming Webhook (Legacy) schema
    # Newer Teams "Workflows" webhooks also typically support "text" top-level.
    payload: dict[str, Any] = {"text": text}
    if extra:
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
