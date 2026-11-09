"""Slack outbound delivery via incoming webhooks."""

from __future__ import annotations

import logging
import os
from typing import Any

from config.constants.slack import SLACK_WEBHOOK_URL_ENV
from infrastructure.delivery.notifications.delivery_transport import post_json
from infrastructure.delivery.notifications.redaction import redact_token
from infrastructure.observability import debug_print

logger = logging.getLogger(__name__)

# Max length for response body excerpts in log messages
_LOG_BODY_MAX_LEN = 200


def _configured_webhook_url() -> str:
    """Return the standalone Slack webhook from env or the local integration store."""
    env_webhook_url = os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip()
    if env_webhook_url:
        return env_webhook_url

    try:
        from integrations.catalog import resolve_effective_integrations

        slack_integration = resolve_effective_integrations().get("slack") or {}
        config = slack_integration.get("config") if isinstance(slack_integration, dict) else {}
        return str(config.get("webhook_url", "") if isinstance(config, dict) else "").strip()
    except Exception:
        logger.debug("Failed to resolve Slack webhook from integration store", exc_info=True)
        return ""


def send_slack_webhook_message(
    text: str,
    *,
    webhook_url: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> tuple[bool, str]:
    """Post a standalone message to a Slack incoming webhook.

    Posts ``text`` (and optional Block Kit ``blocks``) to a Slack incoming
    webhook so any surface can send a Slack notification on demand.

    Args:
        text: Plain-text message body.
        webhook_url: Optional webhook URL. When omitted, resolves from
            ``SLACK_WEBHOOK_URL`` or the local Slack integration store.
        blocks: Optional Slack Block Kit blocks.
        **extra: Any additional Slack payload params merged into the body.

    Returns:
        ``(success, error_detail)``. ``error_detail`` is ``"no_webhook"`` when
        no webhook is configured and ``"webhook=failed"`` when delivery failed.
    """
    url = (webhook_url or _configured_webhook_url()).strip()
    if not url:
        return False, "no_webhook"
    if _post_via_incoming_webhook(text, url, blocks=blocks, **extra):
        return True, ""
    return False, "webhook=failed"


def _post_via_incoming_webhook(
    text: str,
    webhook_url: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> bool:
    """Post a standalone message via Slack incoming webhook."""
    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    if extra:
        payload.update(extra)

    response = post_json(url=webhook_url, payload=payload, timeout=10.0, follow_redirects=True)
    if not response.ok:
        safe_error = redact_token(response.error, webhook_url)
        debug_print(f"Slack incoming webhook failed: {safe_error}")
        return False
    if not 200 <= response.status_code < 300:
        redacted_body = redact_token(response.text, webhook_url)
        safe_body = redacted_body[:_LOG_BODY_MAX_LEN]
        debug_print(f"Slack incoming webhook failed: HTTP {response.status_code}: {safe_body}")
        return False
    debug_print("Slack message posted via incoming webhook.")
    return True
