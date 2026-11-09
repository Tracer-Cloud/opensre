"""Classify Slack store/env credentials into a runtime integration config."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import SlackBotConfig, SlackWebhookConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Accept webhook delivery and/or Socket Mode bot tokens.

    Teammate tools (`slack_read_messages`, roster, …) need ``bot_token`` in the
    resolved integration map. Outbound webhook blast needs ``webhook_url``.
    Either is enough to classify the service as active.
    """
    webhook_url = str(credentials.get("webhook_url") or "").strip()
    bot_token = str(credentials.get("bot_token") or "").strip()
    app_token = str(credentials.get("app_token") or "").strip()
    default_chat_id = str(credentials.get("default_chat_id") or "").strip()
    if not webhook_url and not bot_token:
        return None, None

    config: dict[str, Any] = {}
    if webhook_url:
        try:
            SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
        except Exception as exc:
            report_classify_failure(exc, logger=logger, integration="slack", record_id=record_id)
        else:
            config["webhook_url"] = webhook_url

    if bot_token:
        try:
            bot_cfg = SlackBotConfig.model_validate(
                {
                    "bot_token": bot_token,
                    "app_token": app_token,
                    "signing_secret": str(credentials.get("signing_secret") or "").strip(),
                    "app_id": str(credentials.get("app_id") or "").strip(),
                    "default_chat_id": default_chat_id,
                }
            )
        except Exception as exc:
            report_classify_failure(exc, logger=logger, integration="slack", record_id=record_id)
        else:
            dumped = bot_cfg.model_dump(exclude_none=True)
            config.update({key: value for key, value in dumped.items() if value not in ("", None)})

    if default_chat_id and "default_chat_id" not in config:
        config["default_chat_id"] = default_chat_id

    if not config:
        return None, None
    return config, "slack"
