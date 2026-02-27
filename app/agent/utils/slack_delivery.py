"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.agent.output import debug_print
from app.config import SLACK_CHANNEL

logger = logging.getLogger(__name__)


def _call_reactions_api(method: str, token: str, channel: str, timestamp: str, emoji: str) -> bool:
    """Call Slack reactions.add or reactions.remove.

    Returns True on success, False on expected failures (already_reacted, no_reaction, etc.).
    """
    try:
        resp = httpx.post(
            f"https://slack.com/api/{method}",
            json={"channel": channel, "timestamp": timestamp, "name": emoji},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=8.0,
        )
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            if error not in ("already_reacted", "no_reaction", "message_not_found"):
                logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
        return bool(data.get("ok", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[slack] %s(%s) exception: %s", method, emoji, exc)
        return False


def add_reaction(
    emoji: str,
    channel: str,
    timestamp: str,
    token: str,
) -> None:
    """Add a reaction emoji to a Slack message."""
    _call_reactions_api("reactions.add", token, channel, timestamp, emoji)


def remove_reaction(
    emoji: str,
    channel: str,
    timestamp: str,
    token: str,
) -> None:
    """Remove a reaction emoji from a Slack message (silently ignores if not present)."""
    _call_reactions_api("reactions.remove", token, channel, timestamp, emoji)


def swap_reaction(
    remove_emoji: str,
    add_emoji: str,
    channel: str,
    timestamp: str,
    token: str,
) -> None:
    """Remove one emoji reaction and add another atomically (best-effort)."""
    remove_reaction(remove_emoji, channel, timestamp, token)
    add_reaction(add_emoji, channel, timestamp, token)


def build_action_blocks(investigation_url: str, feedback_url: str | None = None) -> list[dict[str, Any]]:
    """Build Slack Block Kit action blocks with interactive buttons.

    Args:
        investigation_url: URL to the investigation details page in Tracer.
        feedback_url: Optional URL for the feedback form. Defaults to investigation_url.

    Returns:
        List of Block Kit block dicts ready for the blocks parameter.
    """
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View Details in Tracer"},
            "url": investigation_url,
            "style": "primary",
            "action_id": "view_investigation",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "\U0001f4dd Give Feedback"},
            "url": feedback_url or investigation_url,
            "action_id": "give_feedback",
        },
    ]
    return [{"type": "actions", "elements": elements}]


def _merge_payload(
    channel: str,
    text: str,
    thread_ts: str,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build Slack payload by merging base config with optional blocks and any extra keys."""
    payload: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts,
    }
    if blocks:
        payload["blocks"] = blocks
    if extra:
        payload.update(extra)
    return payload


def send_slack_report(
    slack_message: str,
    channel: str | None = None,
    thread_ts: str | None = None,
    access_token: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> None:
    """
    Post the RCA report as a thread reply in Slack.

    Always posts as a thread reply (never a top-level message) to avoid
    triggering the webhook again and creating an infinite loop.

    Args:
        slack_message: The formatted RCA report text.
        channel: Slack channel ID to post to.
        thread_ts: The parent message ts to reply under. Required.
        access_token: Slack bot/user OAuth token for direct posting.
        blocks: Optional Slack Block Kit blocks for interactive elements.
        **extra: Any additional Slack API params (e.g. unfurl_links, mrkdwn) merged into the payload.
    """
    if not thread_ts:
        logger.warning("[slack] Delivery skipped: no thread_ts (channel=%s)", channel)
        debug_print("Slack delivery skipped: no thread_ts - refusing to post top-level message.")
        return

    if access_token and channel:
        success = _post_direct(
            slack_message, channel, thread_ts, access_token, blocks=blocks, **extra
        )
        if not success:
            logger.info("[slack] Direct post failed, falling back to webapp delivery")
            _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks, **extra)
    else:
        _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks, **extra)


def _post_direct(
    text: str,
    channel: str,
    thread_ts: str,
    token: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> bool:
    """Post as a thread reply via Slack chat.postMessage.

    Returns True if the message was posted successfully, False otherwise.
    """
    payload = _merge_payload(channel, text, thread_ts, blocks=blocks, **extra)

    try:
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=15.0,
        )
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            response_meta = data.get("response_metadata", {})
            logger.error(
                "[slack] Direct post FAILED: error=%s, metadata=%s (channel=%s, thread_ts=%s)",
                error, response_meta, channel, thread_ts,
            )
            debug_print(f"Slack direct post failed: {error}")
            return False
        warnings = data.get("response_metadata", {}).get("warnings", [])
        if warnings:
            logger.warning("[slack] Reply posted with warnings: %s", warnings)
        logger.info("[slack] Reply posted successfully (thread_ts=%s, ts=%s)", thread_ts, data.get("ts"))
        debug_print(f"Slack reply posted (thread_ts={thread_ts}, ts={data.get('ts')})")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("[slack] Direct post exception: %s", exc)
        debug_print(f"Slack direct post failed: {exc}")
        return False


def _post_via_webapp(
    text: str,
    channel: str | None,
    thread_ts: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> None:
    """Fallback: delegate to NextJS /api/slack endpoint."""
    base_url = os.getenv("TRACER_API_URL")
    target_channel = channel or SLACK_CHANNEL

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload = _merge_payload(target_channel, text, thread_ts, blocks=blocks, **extra)

    try:
        response = httpx.post(api_url, json=payload, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        debug_print(
            f"Slack delivery failed: HTTP {exc.response.status_code if exc.response else 'unknown'}: {detail[:200]}"
        )
    except Exception as exc:  # noqa: BLE001
        debug_print(f"Slack delivery failed: {exc}")
    else:
        debug_print(f"Slack delivery triggered via NextJS /api/slack (thread_ts={thread_ts}).")
