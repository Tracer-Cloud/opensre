"""Rocket.Chat integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import RocketChatConfig
from integrations.rocketchat.credentials import load_credentials_from_env
from integrations.rocketchat.setup import ROCKETCHAT_SETUP

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[RocketChatConfig | None, str | None]:
    has_auth = (credentials.get("auth_token") or "").strip() or (
        credentials.get("webhook_url") or ""
    ).strip()
    if not has_auth:
        return None, None
    try:
        cfg = RocketChatConfig.model_validate(
            {
                "server_url": credentials.get("server_url", ""),
                "auth_token": credentials.get("auth_token", ""),
                "user_id": credentials.get("user_id", ""),
                "webhook_url": credentials.get("webhook_url", ""),
                "default_channel": credentials.get("default_channel"),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="rocketchat", record_id=record_id)
        return None, None
    return cfg, "rocketchat"


__all__ = [
    "ROCKETCHAT_SETUP",
    "classify",
    "load_credentials_from_env",
]
