"""Discord integration classifier."""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import DiscordBotConfig
from integrations.discord.setup import DISCORD_SETUP

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[DiscordBotConfig | None, str | None]:
    if not (credentials.get("bot_token") or "").strip():
        return None, None
    try:
        cfg = DiscordBotConfig.model_validate(
            {
                "bot_token": credentials.get("bot_token", ""),
                "application_id": credentials.get("application_id", ""),
                "public_key": credentials.get("public_key", ""),
                "default_channel_id": credentials.get("default_channel_id"),
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="discord", record_id=record_id)
        return None, None
    return cfg, "discord"


__all__ = ["DISCORD_SETUP", "classify"]
