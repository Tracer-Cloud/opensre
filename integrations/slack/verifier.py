"""Slack integration verifier.

Single ``VerifierFn``-shaped entry. The user-facing ``--send-slack-test``
flag is plumbed via a private ``_send_slack_test`` key in the config
dict, injected by ``verify.verify_integrations(...)`` before dispatch.
Underscore prefix marks it as runtime-only — never read from on-disk
config.

Accepts either an incoming webhook (outbound delivery) or Socket Mode
tokens (``bot_token`` + ``app_token`` for the two-way gateway), matching
Telegram's "configured credentials verify" shape.
"""

from __future__ import annotations

from typing import Any

import httpx

from infrastructure.delivery.notifications.redaction import redact_slack_token, redact_token
from integrations.config_models import SlackWebhookConfig
from integrations.verification import register_verifier, result

RUNTIME_SEND_TEST_KEY = "_send_slack_test"


def _verify_socket_mode_tokens(config: dict[str, Any], source: str) -> dict[str, str] | None:
    bot_token = str(config.get("bot_token") or "").strip()
    app_token = str(config.get("app_token") or "").strip()
    if not bot_token and not app_token:
        return None
    if not bot_token or not app_token:
        return result(
            "slack",
            source,
            "missing",
            "Socket Mode needs both bot_token (xoxb-…) and app_token (xapp-…).",
        )
    if not bot_token.startswith("xoxb-"):
        return result("slack", source, "failed", "bot_token must start with xoxb-")
    if not app_token.startswith("xapp-"):
        return result("slack", source, "failed", "app_token must start with xapp-")
    try:
        response = httpx.get(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        safe_error = redact_slack_token(str(exc), bot_token)
        return result("slack", source, "failed", f"auth.test failed: {safe_error}")
    if not payload.get("ok"):
        return result(
            "slack",
            source,
            "failed",
            f"auth.test rejected token: {payload.get('error', 'unknown')}",
        )
    team = payload.get("team") or payload.get("url") or "workspace"
    return result(
        "slack",
        source,
        "passed",
        f"Socket Mode tokens look valid (auth.test ok for {team}).",
    )


@register_verifier("slack")
def verify_slack(source: str, config: dict[str, Any]) -> dict[str, str]:
    socket_result = _verify_socket_mode_tokens(config, source)
    webhook_url = str(config.get("webhook_url") or "").strip()

    if not webhook_url:
        if socket_result is not None:
            return socket_result
        return result(
            "slack",
            source,
            "missing",
            "Configure SLACK_WEBHOOK_URL and/or Slack Socket Mode bot_token + app_token.",
        )

    try:
        # Validate the webhook field only — mixed configs also carry Socket
        # Mode keys, which the strict webhook model would reject.
        SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
    except Exception as err:
        return result("slack", source, "missing", str(err))

    if socket_result is not None and socket_result.get("status") != "passed":
        return socket_result

    if not config.get(RUNTIME_SEND_TEST_KEY):
        detail = "Webhook configured."
        if socket_result is not None:
            detail = f"{detail} {socket_result.get('detail', '')}".strip()
        else:
            detail = f"{detail} Use --send-slack-test to validate delivery."
        return result("slack", source, "passed", detail)

    payload = {"text": "Tracer integration test: Slack webhook is configured correctly."}
    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0)
        response.raise_for_status()
    except Exception as exc:
        safe_error = redact_token(str(exc), webhook_url)
        return result("slack", source, "failed", f"Webhook delivery failed: {safe_error}")
    return result("slack", source, "passed", "Webhook delivered test message successfully.")
