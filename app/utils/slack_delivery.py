"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from app.config import SLACK_CHANNEL
from app.output import debug_print

logger = logging.getLogger(__name__)

_SLACK_TOKEN_RE = re.compile(r"(xox[bapr]-[0-9a-zA-Z-]+)")


def _redact_arg(a: object) -> object:
    """Redact Slack token from a log arg, preserving the original type if no match."""
    s = str(a)
    redacted = _SLACK_TOKEN_RE.sub("<redacted>", s)
    return redacted if redacted != s else a


class _SlackTokenFilter(logging.Filter):
    """Scrub Slack tokens from httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _SLACK_TOKEN_RE.sub("<redacted>", str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def _install_httpx_token_filter() -> None:
    _filter = _SlackTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_filter)


_install_httpx_token_filter()


def _redact_token(text: str, token: str) -> str:
    if token and token in text:
        return text.replace(token, "<redacted>")
    return text


def _response_error_message(resp: Any) -> str:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return str(getattr(resp, "text", "")) or f"HTTP {getattr(resp, 'status_code', 'unknown')}"

    if isinstance(data, dict):
        for key in ("error", "message", "detail", "description"):
            value = data.get(key)
            if value:
                return str(value)
        return "unknown"

    return str(data) if data else f"HTTP {getattr(resp, 'status_code', 'unknown')}"


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
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            error = _redact_token(_response_error_message(resp), token)
            logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
            return False
        if not isinstance(data, dict):
            error = _redact_token(_response_error_message(resp), token)
            logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
            return False
        if not data.get("ok"):
            error = _redact_token(str(data.get("error", "unknown")), token)
            if error not in ("already_reacted", "no_reaction", "message_not_found"):
                logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
        return bool(data.get("ok", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[slack] %s(%s) exception: %s", method, emoji, _redact_token(str(exc), token))
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
            logger.info(
                "[slack] Direct post failed (%s), falling back to webapp delivery", direct_error
            )
            webapp_ok = _post_via_webapp(slack_message, channel, thread_ts, blocks=blocks, **extra)
            if not webapp_ok:
                return False, f"direct={direct_error}, webapp=failed"
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
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            error = _redact_token(_response_error_message(resp), token)
            logger.error(
                "[slack] Direct post FAILED: non-JSON response detail=%s (channel=%s, thread_ts=%s)",
                error,
                channel,
                thread_ts,
            )
            return False, error
        if not isinstance(data, dict):
            error = _redact_token(_response_error_message(resp), token)
            logger.error(
                "[slack] Direct post FAILED: unexpected response detail=%s (channel=%s, thread_ts=%s)",
                error,
                channel,
                thread_ts,
            )
            return False, error
        if not data.get("ok"):
            error = _redact_token(str(data.get("error", "unknown")), token)
            response_meta = data.get("response_metadata", {})
            logger.error(
                "[slack] Direct post FAILED: error=%s, metadata=%s (channel=%s, thread_ts=%s)",
                error,
                response_meta,
                channel,
                thread_ts,
            )
            return False, f"slack_error={error}"
        warnings = data.get("response_metadata", {}).get("warnings", [])
        if warnings:
            logger.warning("[slack] Reply posted with warnings: %s", warnings)
        logger.info(
            "[slack] Reply posted successfully (thread_ts=%s, ts=%s)", thread_ts, data.get("ts")
        )
        return True, ""
    except Exception as exc:  # noqa: BLE001
        error = _redact_token(str(exc), token)
        logger.error(
            "[slack] Direct post exception type=%s channel=%s thread_ts=%s detail=%s "
            "(caller may attempt fallback)",
            type(exc).__name__,
            channel,
            thread_ts,
            error,
        )
        return False, f"exception={error}"


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

    try:
        response = httpx.post(api_url, json=payload, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        debug_print(
            f"Slack delivery failed: HTTP {exc.response.status_code if exc.response else 'unknown'}: {detail[:200]}"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        debug_print(f"Slack delivery failed: {exc}")
        return False
    else:
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

    try:
        response = httpx.post(webhook_url, json=payload, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        debug_print(
            f"Slack incoming webhook failed: HTTP {exc.response.status_code if exc.response else 'unknown'}: {detail[:200]}"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        debug_print(f"Slack incoming webhook failed: {exc}")
        return False
    else:
        debug_print("Slack report posted via incoming webhook.")
        return True
