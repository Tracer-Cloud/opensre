"""Read a Slack thread's messages for inclusion in runtime investigations.

This module provides a narrow helper — not a full Slack integration. It lets
``opensre investigate --service <name> --slack-thread <ref>`` pull the text
of a specific Slack thread so the investigation agent has that conversational
context alongside service logs and health.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SLACK_API_URL = "https://slack.com/api/conversations.replies"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MESSAGE_LIMIT = 50


def parse_slack_thread_ref(ref: str) -> tuple[str, str]:
    """Parse a ``CHANNEL/TS`` reference into ``(channel, ts)``.

    Raises ``ValueError`` if the reference is not well-formed.
    """
    value = (ref or "").strip()
    if "/" not in value:
        raise ValueError(
            f"Expected Slack thread ref as 'CHANNEL/TS' (e.g. C0123/1712345.000001), got: {ref!r}"
        )
    channel, ts = value.split("/", 1)
    channel = channel.strip()
    ts = ts.strip()
    if not channel or not ts:
        raise ValueError(f"Slack thread ref must have both CHANNEL and TS, got: {ref!r}")
    return channel, ts


def fetch_slack_thread(
    channel: str,
    ts: str,
    bot_token: str,
    *,
    limit: int = _DEFAULT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    """Fetch a Slack thread's messages via ``conversations.replies``.

    Returns a dict with either ``{"error": "..."}`` on any failure, or
    ``{"channel", "ts", "messages": [...], "count": int}`` on success.
    """
    if not bot_token:
        return {"error": "SLACK_BOT_TOKEN not configured"}

    try:
        resp = httpx.get(
            _SLACK_API_URL,
            params={"channel": channel, "ts": ts, "limit": min(limit, 100)},
            headers={"Authorization": f"Bearer {bot_token}"},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("[slack] thread fetch HTTP error status=%s", exc.response.status_code)
        return {"error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        logger.warning("[slack] thread fetch error: %s", exc)
        return {"error": str(exc)}

    if not data.get("ok"):
        return {"error": data.get("error", "slack API returned ok=false")}

    messages: list[dict[str, Any]] = []
    for msg in data.get("messages", []):
        messages.append(
            {
                "user": msg.get("user", ""),
                "text": msg.get("text", ""),
                "ts": msg.get("ts", ""),
                "reactions": [
                    r.get("name", "")
                    for r in msg.get("reactions", [])
                    if isinstance(r, dict)
                ],
            }
        )

    return {
        "channel": channel,
        "ts": ts,
        "messages": messages,
        "count": len(messages),
    }
