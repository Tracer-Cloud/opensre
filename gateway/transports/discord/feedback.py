"""Reply feedback buttons on Discord final answers."""

from __future__ import annotations

import logging
import time
from typing import Any

import discord

from config.constants import OPENSRE_HOME_DIR
from gateway.core.storage.feedback import append_feedback_entry

logger = logging.getLogger("gateway")

FEEDBACK_GOOD_ID = "opensre_reply_feedback:good"
FEEDBACK_BAD_ID = "opensre_reply_feedback:bad"

_DEFAULT_FEEDBACK_PATH = OPENSRE_HOME_DIR / "gateway" / "discord_feedback.jsonl"


def feedback_components() -> list[dict[str, Any]]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "Helpful",
                    "custom_id": FEEDBACK_GOOD_ID,
                },
                {
                    "type": 2,
                    "style": 2,
                    "label": "Not helpful",
                    "custom_id": FEEDBACK_BAD_ID,
                },
            ],
        }
    ]


def record_feedback_interaction(interaction: discord.Interaction) -> bool:
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data
    if not isinstance(data, dict):
        return False
    custom_id = str(data.get("custom_id") or "")
    if custom_id == FEEDBACK_GOOD_ID:
        verdict = "good"
    elif custom_id == FEEDBACK_BAD_ID:
        verdict = "bad"
    else:
        return False
    message = interaction.message
    entry = {
        "ts": time.time(),
        "platform": "discord",
        "user_id": str(interaction.user.id),
        "channel_id": str(interaction.channel_id or ""),
        "message_id": str(message.id) if message is not None else "",
        "verdict": verdict,
    }
    if not append_feedback_entry(entry, path=_DEFAULT_FEEDBACK_PATH):
        return False
    logger.info(
        "[discord-gateway] reply feedback verdict=%s channel=%s",
        verdict,
        entry["channel_id"],
    )
    return True


__all__ = ["feedback_components", "record_feedback_interaction"]
