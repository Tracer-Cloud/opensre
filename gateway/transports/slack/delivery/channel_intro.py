"""First-join channel intro: greet once when the bot is invited to a channel.

Claude Tag posts a short intro on its own when invited to a channel; Slack's
`member_joined_channel` event makes this one handler. The intro explains the
three behaviors people otherwise discover by trial and error: mention to
start, thread-following after a mention, and approval prompts before writes.
Needs the `member_joined_channel` event subscription on the app manifest.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any

from gateway.transports.slack.client import SlackMessagingClient

logger = logging.getLogger("gateway")

# Rejoining a channel after this many other channels were greeted may greet
# again — the set is a per-process spam guard, not a persistent record.
_MAX_GREETED_CHANNELS = 1024


def _intro_text(bot_user_id: str) -> str:
    me = f"<@{bot_user_id}>" if bot_user_id else "me"
    return (
        f"👋 Hi, I'm {me} — an SRE teammate.\n"
        f"• Mention {me} with a question or an incident and I'll help "
        "in a thread: this channel's history, Sentry, Grafana, Kubernetes, "
        "logs.\n"
        "• Once you mention me in a thread, I follow the conversation — no "
        "need to re-tag every message.\n"
        "• High-impact actions such as joining channels or applying code "
        "fixes ask for your approval first."
    )


class ChannelIntroGreeter:
    """Posts one intro message per channel the bot joins (per process)."""

    def __init__(self, *, messaging: SlackMessagingClient, bot_user_id: str) -> None:
        self._messaging = messaging
        self._bot_user_id = bot_user_id
        self._greeted: set[str] = set()
        self._lock = threading.Lock()

    def handle(self, payload: Mapping[str, Any]) -> bool:
        """Greet if this Events API payload is the bot joining a channel."""
        event = payload.get("event")
        if not isinstance(event, Mapping):
            return False
        if str(event.get("type") or "") != "member_joined_channel":
            return False
        if not self._bot_user_id or str(event.get("user") or "") != self._bot_user_id:
            return False
        channel_id = str(event.get("channel") or "")
        if not channel_id:
            return False
        with self._lock:
            if channel_id in self._greeted:
                return False
            if len(self._greeted) >= _MAX_GREETED_CHANNELS:
                self._greeted.clear()
            self._greeted.add(channel_id)
        # Plain mrkdwn text (no markdown block): `<@U…>` mention tokens are
        # guaranteed to render in the text field.
        posted = (
            self._messaging.post_message(channel=channel_id, text=_intro_text(self._bot_user_id))
            is not None
        )
        if posted:
            logger.info("[slack-gateway] posted channel intro channel=%s", channel_id)
        else:
            # Allow a retry on the next join event rather than staying silent.
            with self._lock:
                self._greeted.discard(channel_id)
            logger.warning("[slack-gateway] channel intro post failed channel=%s", channel_id)
        return posted


__all__ = ["ChannelIntroGreeter"]
