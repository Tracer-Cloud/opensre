"""Slack channel polling utilities for test verification."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


def get_channel_id(channel_name: str = "devs-alerts") -> str | None:
    """Resolve a Slack channel name to its ID.

    Checks SLACK_DEVS_ALERTS_CHANNEL_ID env var first, then falls back
    to the conversations.list API.
    """
    channel_id = os.environ.get("SLACK_DEVS_ALERTS_CHANNEL_ID", "")
    if channel_id:
        return channel_id

    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return None

    url = "https://slack.com/api/conversations.list?types=public_channel,private_channel&limit=200"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    for ch in data.get("channels", []):
        if ch["name"] == channel_name:
            return ch["id"]
    return None


def get_recent_messages(channel_id: str, oldest: str = "0") -> list[dict[str, Any]]:
    """Fetch recent messages from a Slack channel."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return []

    url = (
        f"https://slack.com/api/conversations.history?channel={channel_id}&oldest={oldest}&limit=10"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data.get("messages", [])


def _extract_searchable_text(msg: dict[str, Any]) -> str:
    """Extract all searchable text from a Slack message, including attachments and blocks."""
    parts: list[str] = []
    parts.append(msg.get("text", ""))

    for att in msg.get("attachments", []):
        parts.append(att.get("text", ""))
        parts.append(att.get("fallback", ""))
        parts.append(att.get("pretext", ""))
        parts.append(att.get("title", ""))
        for field in att.get("fields", []):
            parts.append(field.get("value", ""))

    for block in msg.get("blocks", []):
        if block.get("type") == "section":
            t = block.get("text", {})
            parts.append(t.get("text", "") if isinstance(t, dict) else str(t))

    return " ".join(parts).lower()


def poll_for_message(
    keywords: list[str],
    *,
    channel_id: str | None = None,
    channel_name: str = "devs-alerts",
    max_wait: int = 300,
    poll_interval: int = 10,
    since_epoch: float | None = None,
) -> bool:
    """Poll a Slack channel until a message containing any keyword appears.

    Args:
        since_epoch: Unix timestamp to look for messages from. Defaults to 5 minutes
            before the poll starts. Pass the script's start time for best results.

    Returns True if a matching message was found within the timeout.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK_BOT_TOKEN not set, skipping Slack verification")
        return False

    if not channel_id:
        channel_id = get_channel_id(channel_name)
    if not channel_id:
        print(f"Could not find #{channel_name} channel. Set SLACK_DEVS_ALERTS_CHANNEL_ID.")
        return False

    oldest = str(since_epoch or (time.time() - 300))
    print(f"Polling Slack #{channel_name} (up to {max_wait}s)...")
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        messages = get_recent_messages(channel_id, oldest=oldest)
        for msg in messages:
            searchable = _extract_searchable_text(msg)
            if any(kw.lower() in searchable for kw in keywords):
                preview = searchable[:200]
                print("  Matching message found in Slack!")
                print(f"  Preview: {preview}")
                return True

        remaining = int(deadline - time.monotonic())
        print(f"  No match yet, retrying... ({remaining}s remaining)")
        time.sleep(poll_interval)

    print(f"No matching message appeared in Slack within {max_wait}s")
    return False
