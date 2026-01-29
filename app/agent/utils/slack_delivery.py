"""Slack delivery helper that delegates posting to the NextJS app."""

from __future__ import annotations

import os
import sys

import httpx

from app.agent.output import debug_print


def send_slack_report(slack_message: str) -> None:
    """
    Send the final Slack message via the existing NextJS /api/slack endpoint.

    The Python agent never talks to Slack directly; it hands the message to the
    web app which posts to Slack using its bot token.
    """
    # if not slack_message:
    #     return
    
    print("[slack] send_slack_report called", file=sys.stderr)

    base_url = os.getenv("TRACER_API_URL")

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload = {"channel": 'tracer-rca-report-alerts', "text": slack_message}

    try:
        response = httpx.post(api_url, json=payload, timeout=10.0, follow_redirects=True)
        print(f"[slack] POST {api_url} -> {response.status_code} {response.text}", file=sys.stderr)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        print(
            f"[slack] Delivery failed ({exc.response.status_code if exc.response else 'unknown'}): {detail}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort logging, no crash
        print(f"[slack] Delivery failed: {exc}", file=sys.stderr)
    else:
        debug_print("Slack delivery triggered via NextJS /api/slack.")
