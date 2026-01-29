"""Slack delivery helper that delegates posting to the NextJS app."""

from __future__ import annotations

import os

import httpx

from app.agent.output import debug_print


def send_slack_report(slack_message: str) -> None:
    """
    Send the final Slack message via the existing NextJS /api/slack endpoint.

    The Python agent never talks to Slack directly; it hands the message to the
    web app which posts to Slack using its bot token.
    """
    base_url = os.getenv("TRACER_API_URL")
    slack_channel = os.getenv("SLACK_CHANNEL")

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload = {"channel": slack_channel, "text": slack_message}

    try:
        response = httpx.post(api_url, json=payload, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        debug_print(f"Slack delivery failed: HTTP {exc.response.status_code if exc.response else 'unknown'}: {detail[:200]}")
    except Exception as exc:  # noqa: BLE001 - best-effort logging, no crash
        debug_print(f"Slack delivery failed: {exc}")
    else:
        debug_print("Slack delivery triggered via NextJS /api/slack.")
