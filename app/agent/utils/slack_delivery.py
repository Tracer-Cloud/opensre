"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.agent.output import debug_print
from app.config import SLACK_CHANNEL


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


def send_slack_report(
    slack_message: str,
    channel: str | None = None,
    thread_ts: str | None = None,
    access_token: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
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
    """
    if not thread_ts:
        debug_print("Slack delivery skipped: no thread_ts - refusing to post top-level message.")
        return

    if access_token and channel:
        _post_direct(slack_message, channel, thread_ts, access_token, blocks=blocks)
    else:
        _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks)


def _post_direct(
    text: str, channel: str, thread_ts: str, token: str, *, blocks: list[dict[str, Any]] | None = None,
) -> None:
    """Post as a thread reply via Slack chat.postMessage."""
    payload: dict[str, Any] = {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts,
    }
    if blocks:
        payload["blocks"] = blocks

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
            debug_print(f"Slack direct post failed: {data.get('error')}")
        else:
            debug_print(f"Slack reply posted (thread_ts={thread_ts}, ts={data.get('ts')})")
    except Exception as exc:  # noqa: BLE001
        debug_print(f"Slack direct post failed: {exc}")


def _post_via_webapp(
    text: str, channel: str | None, thread_ts: str, *, blocks: list[dict[str, Any]] | None = None,
) -> None:
    """Fallback: delegate to NextJS /api/slack endpoint."""
    base_url = os.getenv("TRACER_API_URL")
    target_channel = channel or SLACK_CHANNEL

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload: dict[str, Any] = {
        "channel": target_channel,
        "text": text,
        "thread_ts": thread_ts,
    }
    if blocks:
        payload["blocks"] = blocks

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
