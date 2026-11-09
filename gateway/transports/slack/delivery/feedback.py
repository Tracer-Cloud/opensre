"""Reply feedback: 👍/👎 buttons on final answers, clicks recorded to JSONL.

Slack's AI-app guidance recommends feedback buttons "with every message"
(Block Kit ``feedback_buttons`` inside a ``context_actions`` block). Clicks
arrive as ``block_actions`` on the same interactive Socket Mode envelopes the
approval gate uses. Recorded feedback is the ground truth for tuning the
attention gate later (e.g. whether a layer-4 micro-classifier is warranted).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR
from gateway.core.storage.feedback import append_feedback_entry

logger = logging.getLogger("gateway")

FEEDBACK_ACTION_ID = "opensre_reply_feedback"
FEEDBACK_POSITIVE_VALUE = "good"
FEEDBACK_NEGATIVE_VALUE = "bad"

_DEFAULT_FEEDBACK_PATH = OPENSRE_HOME_DIR / "gateway" / "slack_feedback.jsonl"


def feedback_block() -> dict[str, Any]:
    """The 👍/👎 ``context_actions`` block appended to final replies."""
    return {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": FEEDBACK_ACTION_ID,
                "positive_button": {
                    "text": {"type": "plain_text", "text": "Good response"},
                    "accessibility_label": "Mark this response as helpful",
                    "value": FEEDBACK_POSITIVE_VALUE,
                },
                "negative_button": {
                    "text": {"type": "plain_text", "text": "Bad response"},
                    "accessibility_label": "Mark this response as unhelpful",
                    "value": FEEDBACK_NEGATIVE_VALUE,
                },
            }
        ],
    }


def record_feedback_payload(
    payload: Mapping[str, Any],
    *,
    path: Path | None = None,
) -> bool:
    """Append feedback clicks from a ``block_actions`` payload to the JSONL log.

    Returns whether at least one feedback action was recorded. Any workspace
    member's feedback counts — unlike approvals this is telemetry, not a
    privileged action.
    """
    if str(payload.get("type") or "") != "block_actions":
        return False
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return False
    user_id = str((payload.get("user") or {}).get("id") or "")
    channel_id = str((payload.get("channel") or {}).get("id") or "")
    message_ts = str((payload.get("message") or {}).get("ts") or "")
    recorded = False
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        if str(action.get("action_id") or "") != FEEDBACK_ACTION_ID:
            continue
        entry = {
            "ts": time.time(),
            "platform": "slack",
            "user_id": user_id,
            "channel_id": channel_id,
            "message_ts": message_ts,
            # The clicked button's value ("good"/"bad"); keep the raw action
            # value field only — never message content (Slack data-retention
            # guidance: store metadata, not data).
            "verdict": str(action.get("value") or ""),
        }
        if append_feedback_entry(entry, path=path or _DEFAULT_FEEDBACK_PATH):
            recorded = True
            logger.info(
                "[slack-gateway] reply feedback verdict=%s channel=%s message_ts=%s",
                entry["verdict"],
                channel_id,
                message_ts,
            )
    return recorded


__all__ = [
    "FEEDBACK_ACTION_ID",
    "FEEDBACK_NEGATIVE_VALUE",
    "FEEDBACK_POSITIVE_VALUE",
    "feedback_block",
    "record_feedback_payload",
]
