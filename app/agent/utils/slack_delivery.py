"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import os

import httpx

from app.agent.output import debug_print
from app.config import SLACK_CHANNEL


def send_slack_report(
    slack_message: str,
    channel: str | None = None,
    thread_ts: str | None = None,
    access_token: str | None = None,
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
    """
    if not thread_ts:
        debug_print("Slack delivery skipped: no thread_ts - refusing to post top-level message.")
        return

    if access_token and channel:
        _post_direct(slack_message, channel, thread_ts, access_token)
    else:
        _post_via_webapp(slack_message, channel, thread_ts)


def _post_direct(
    text: str, channel: str, thread_ts: str, token: str
) -> None:
    """Post as a thread reply via Slack chat.postMessage."""
    payload: dict[str, str] = {
        "channel": channel,
        "text": text,
        "thread_ts": thread_ts,
    }

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
    text: str, channel: str | None, thread_ts: str
) -> None:
    """Fallback: delegate to NextJS /api/slack endpoint."""
    base_url = os.getenv("TRACER_API_URL")
    target_channel = channel or SLACK_CHANNEL

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload: dict[str, str] = {
        "channel": target_channel,
        "text": text,
        "thread_ts": thread_ts,
    }

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
