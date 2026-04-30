"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.config import SLACK_CHANNEL
from app.output import debug_print
from app.utils.delivery_transport import post_json

logger = logging.getLogger(__name__)

_ACCESS_TOKEN_RE = re.compile(r"(xox[baprs]-)[A-Za-z0-9-]+")


def _redact_token(text: str, access_token: str) -> str:
    """Replace ``access_token`` with ``<redacted>`` to prevent accidental log/error leakage."""
    redacted = text
    if access_token and access_token in text:
        redacted = text.replace(access_token, "<redacted>")
    return _ACCESS_TOKEN_RE.sub(r"\1<redacted>", redacted)


def _extract_error(data: dict[str, Any], status_code: int, text: str) -> str:
    """Return a human-readable error string from a Slack API response.

    Tries ``data["error"]`` first, then falls back to the raw response body
    or the HTTP status code so non-JSON failure bodies (HTML, plain text)
    never cause a crash.
    """
    error = data.get("error")
    if error:
        return str(error)
    if text:
        return text[:500]
    return f"HTTP {status_code}"


def _slack_bearer_headers(token: str) -> dict[str, str]:
    # Slack explicitly recommends ``charset=utf-8`` on JSON POSTs — without
    # it the API replies with a ``missing_charset`` warning in
    # ``response_metadata.warnings``. httpx only auto-sets the bare
    # ``application/json`` (no charset) for ``json=`` kwargs, so we keep
    # the explicit charset header here.
    # See https://api.slack.com/web#posting_json
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _call_reactions_api(method: str, token: str, channel: str, timestamp: str, emoji: str) -> bool:
    """Call Slack reactions.add or reactions.remove.

    Returns True on success, False on expected failures (already_reacted, no_reaction, etc.).
    """
    response = post_json(
        url=f"https://slack.com/api/{method}",
        payload={"channel": channel, "timestamp": timestamp, "name": emoji},
        headers=_slack_bearer_headers(token),
        timeout=8.0,
    )
    if not response.ok:
        safe_error = _redact_token(response.error, token)
        logger.warning("[slack] %s(%s) exception: %s", method, emoji, safe_error)
        return False
    if not response.data.get("ok"):
        error = response.data.get("error", "unknown")
        if error not in ("already_reacted", "no_reaction", "message_not_found"):
            logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
    return bool(response.data.get("ok", False))


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


def build_action_blocks(
    investigation_url: str, investigation_id: str | None = None
) -> list[dict[str, Any]]:
    """Build Slack Block Kit action blocks with interactive buttons.

    Args:
        investigation_url: URL to the investigation details page in Tracer.
        investigation_id: Investigation ID embedded in feedback option values so the
            interactivity handler can update the correct record.

    Returns:
        List of Block Kit block dicts ready for the blocks parameter.
    """
    feedback_options = [
        {
            "text": {"type": "plain_text", "text": "\U0001f44d Accurate"},
            "value": f"accurate|{investigation_id or ''}",
        },
        {
            "text": {"type": "plain_text", "text": "\U0001f914 Partially accurate"},
            "value": f"partial|{investigation_id or ''}",
        },
        {
            "text": {"type": "plain_text", "text": "\U0001f44e Inaccurate"},
            "value": f"inaccurate|{investigation_id or ''}",
        },
    ]
    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View Details in Tracer"},
            "url": investigation_url,
            "style": "primary",
            "action_id": "view_investigation",
        },
        {
            "type": "static_select",
            "placeholder": {"type": "plain_text", "text": "\U0001f4dd Give Feedback"},
            "action_id": "give_feedback",
            "options": feedback_options,
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
) -> tuple[bool, str]:
    """
    Post the RCA report as a thread reply in Slack.

    When thread context is available, prefers a thread reply to avoid creating
    loops for inbound Slack-triggered investigations. For standalone CLI or
    local investigations, falls back to SLACK_WEBHOOK_URL if configured.

    Args:
        slack_message: The formatted RCA report text.
        channel: Slack channel ID to post to.
        thread_ts: The parent message ts to reply under. Required.
        access_token: Slack bot/user OAuth token for direct posting.
        blocks: Optional Slack Block Kit blocks for interactive elements.
        **extra: Any additional Slack API params (e.g. unfurl_links, mrkdwn) merged into the payload.

    Returns:
        (success, error_detail) — success is True if posted, error_detail is non-empty on failure.
    """
    if not thread_ts:
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if webhook_url:
            webhook_ok = _post_via_incoming_webhook(
                slack_message,
                webhook_url,
                blocks=blocks,
                **extra,
            )
            return (True, "") if webhook_ok else (False, "webhook=failed")
        logger.debug("[slack] Delivery skipped: no thread_ts (channel=%s)", channel)
        debug_print("Slack delivery skipped: no thread_ts and no SLACK_WEBHOOK_URL configured.")
        return False, "no_thread_ts"

    if access_token and channel:
        success, direct_error = _post_direct(
            slack_message, channel, thread_ts, access_token, blocks=blocks, **extra
        )
        if not success:
            safe_error = _redact_token(direct_error, access_token)
            logger.info(
                "[slack] Direct post failed (%s), falling back to webapp delivery", safe_error
            )
            webapp_ok = _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks, **extra)
            if not webapp_ok:
                return False, f"direct={safe_error}, webapp=failed"
            return True, ""
        return True, ""
    else:
        webapp_ok = _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks, **extra)
        return (True, "") if webapp_ok else (False, "webapp=failed")


def _post_direct(
    text: str,
    channel: str,
    thread_ts: str,
    token: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> tuple[bool, str]:
    """Post as a thread reply via Slack chat.postMessage.

    Returns (success, error_detail) where error_detail is empty on success.
    """
    payload = _merge_payload(channel, text, thread_ts, blocks=blocks, **extra)
    response = post_json(
        url="https://slack.com/api/chat.postMessage",
        payload=payload,
        headers=_slack_bearer_headers(token),
    )
    if not response.ok:
        safe_error = _redact_token(response.error, token)
        logger.error(
            "[slack] Direct post exception type=%s channel=%s thread_ts=%s detail=%s "
            "(caller may attempt fallback)",
            response.exc_type or "Exception",
            channel,
            thread_ts,
            safe_error,
        )
        return False, f"exception={safe_error}"
    if response.data.get("ok") is not True:
        error = response.data.get("error")
        if not error:
            error = _extract_error(dict(response.data), response.status_code, response.text)
        safe_error = _redact_token(str(error), token)
        response_meta = response.data.get("response_metadata", {})
        logger.error(
            "[slack] Direct post FAILED: error=%s, metadata=%s (channel=%s, thread_ts=%s)",
            safe_error,
            response_meta,
            channel,
            thread_ts,
        )
        return False, f"slack_error={safe_error}"
    warnings = response.data.get("response_metadata", {}).get("warnings", [])
    if warnings:
        logger.warning("[slack] Reply posted with warnings: %s", warnings)
    logger.info(
        "[slack] Reply posted successfully (thread_ts=%s, ts=%s)",
        thread_ts,
        response.data.get("ts"),
    )
    return True, ""


def _post_via_webapp(
    text: str,
    channel: str | None,
    thread_ts: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> bool:
    """Fallback: delegate to NextJS /api/slack endpoint.

    Returns True if the message was delivered successfully, False otherwise.
    """
    base_url = os.getenv("TRACER_API_URL")
    target_channel = channel or SLACK_CHANNEL

    if not base_url:
        debug_print("Slack delivery skipped: TRACER_API_URL not set.")
        return False

    api_url = f"{base_url.rstrip('/')}/api/slack"
    payload = _merge_payload(target_channel, text, thread_ts, blocks=blocks, **extra)
    response = post_json(url=api_url, payload=payload, timeout=10.0, follow_redirects=True)
    if not response.ok:
        debug_print(f"Slack delivery failed: {response.error}")
        return False
    if not 200 <= response.status_code < 300:
        debug_print(f"Slack delivery failed: HTTP {response.status_code}: {response.text[:200]}")
        return False
    debug_print(f"Slack delivery triggered via NextJS /api/slack (thread_ts={thread_ts}).")
    return True


def _post_via_incoming_webhook(
    text: str,
    webhook_url: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> bool:
    """Post a standalone RCA report via Slack incoming webhook."""
    payload: dict[str, Any] = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    if extra:
        payload.update(extra)

    response = post_json(url=webhook_url, payload=payload, timeout=10.0, follow_redirects=True)
    if not response.ok:
        debug_print(f"Slack incoming webhook failed: {response.error}")
        return False
    if not 200 <= response.status_code < 300:
        debug_print(
            f"Slack incoming webhook failed: HTTP {response.status_code}: {response.text[:200]}"
        )
        return False
    debug_print("Slack report posted via incoming webhook.")
    return True
