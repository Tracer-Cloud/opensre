"""Slack delivery helper - posts directly to Slack API or delegates to NextJS."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.config import SLACK_CHANNEL
from app.output import debug_print
from app.utils.delivery_transport import post_json, redact_arg, redact_token

logger = logging.getLogger(__name__)

_SLACK_TOKEN_RE = re.compile(r"(xox[a-z]-[0-9a-zA-Z-]+|hooks\.slack\.com/services/[a-zA-Z0-9/]+)")


def _redact_arg(a: object) -> object:
    return redact_arg(a, _SLACK_TOKEN_RE)


def _redact_token(text: str, token: str) -> str:
    return redact_token(text, token)


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
    for name in ("httpx", "httpcore"):
        lgr = logging.getLogger(name)
        if not any(isinstance(f, _SlackTokenFilter) for f in lgr.filters):
            lgr.addFilter(_SlackTokenFilter())


_install_httpx_token_filter()


def _slack_bearer_headers(token: str) -> dict[str, str]:
    """Build Slack auth headers.

    Content-Type must include charset=utf-8 — Slack emits ``missing_charset``
    warnings without it.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _call_reactions_api(method: str, token: str, channel: str, timestamp: str, emoji: str) -> bool:
    response = post_json(
        url=f"https://slack.com/api/{method}",
        payload={"channel": channel, "timestamp": timestamp, "name": emoji},
        headers=_slack_bearer_headers(token),
        timeout=8.0,
    )
    if not response.ok:
        error = _redact_token(response.error, token)
        logger.warning("[slack] %s(%s) exception: %s", method, emoji, error)
        return False

    if not response.data.get("ok"):
        error = _redact_token(str(response.data.get("error", "unknown")), token)
        if error not in ("already_reacted", "no_reaction", "message_not_found"):
            logger.warning("[slack] %s(%s) failed: %s", method, emoji, error)
            return False
<<<<<<< HEAD
        # idempotent no-ops are silenced but still treated as success
        return True
=======
        # idempotent no-ops are silenced but still return False to match old behavior
        return False
>>>>>>> fix/delivery-hardening-clean

    return True


def add_reaction(emoji: str, channel: str, timestamp: str, token: str) -> None:
    """Add a reaction emoji to a Slack message."""
    _call_reactions_api("reactions.add", token, channel, timestamp, emoji)


def remove_reaction(emoji: str, channel: str, timestamp: str, token: str) -> None:
    """Remove a reaction emoji from a Slack message (silently ignores if not present)."""
    _call_reactions_api("reactions.remove", token, channel, timestamp, emoji)


def swap_reaction(
    remove_emoji: str, add_emoji: str, channel: str, timestamp: str, token: str
) -> None:
    """Remove one emoji reaction and add another atomically (best-effort)."""
    remove_reaction(remove_emoji, channel, timestamp, token)
    add_reaction(add_emoji, channel, timestamp, token)


def build_action_blocks(
    investigation_url: str, investigation_id: str | None = None
) -> list[dict[str, Any]]:
    """Build Slack Block Kit action blocks with interactive buttons."""
    feedback_options = [
        {
            "text": {"type": "plain_text", "text": "👍 Accurate"},
            "value": f"accurate|{investigation_id or ''}",
        },
        {
            "text": {"type": "plain_text", "text": "🤔 Partially accurate"},
            "value": f"partial|{investigation_id or ''}",
        },
        {
            "text": {"type": "plain_text", "text": "👎 Inaccurate"},
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
            "placeholder": {"type": "plain_text", "text": "📝 Give Feedback"},
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
    payload: dict[str, Any] = {"channel": channel, "text": text, "thread_ts": thread_ts}
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
    """Post the RCA report as a thread reply in Slack."""
    if not thread_ts:
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if webhook_url:
            webhook_ok = _post_via_incoming_webhook(
                slack_message, webhook_url, blocks=blocks, **extra
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
    """Post as a thread reply via Slack chat.postMessage."""
    payload = _merge_payload(channel, text, thread_ts, blocks=blocks, **extra)
    response = post_json(
        url="https://slack.com/api/chat.postMessage",
        payload=payload,
        headers=_slack_bearer_headers(token),
        timeout=15.0,
    )
    if not response.ok:
        error = _redact_token(response.error, token)
        logger.error(
            "[slack] Direct post EXCEPTION type=%s channel=%s thread_ts=%s detail=%s "
            "(caller may attempt fallback)",
            response.exc_type or "Exception",
            channel,
            thread_ts,
            error,
        )
        return False, f"exception={error}"

    if not response.data.get("ok"):
        error = _redact_token(str(response.data.get("error", "unknown")), token)
        response_meta = response.data.get("response_metadata", {})
        logger.error(
            "[slack] Direct post FAILED: error=%s, metadata=%s (channel=%s, thread_ts=%s)",
            error,
            response_meta,
            channel,
            thread_ts,
        )
        return False, f"slack_error={error}"

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
    """Fallback: delegate to NextJS /api/slack endpoint."""
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
            f"Slack incoming webhook failed: {response.exc_type or response.error or 'unknown'}"
        )
        return False

    debug_print("Slack report posted via incoming webhook.")
    return True
