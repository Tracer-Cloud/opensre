from __future__ import annotations

from typing import Any

from infrastructure.delivery.notifications.redaction import redact_slack_token
from integrations.slack.web_client import _request_json, resolve_bot_token

_PAGE_SIZE = 50
_MAX_MESSAGES = 500


def parse_thread_ref(thread_ref: str) -> tuple[str, str]:
    channel, separator, timestamp = thread_ref.strip().partition("/")
    if not separator or not channel or not timestamp:
        raise ValueError("thread_ref must use CHANNEL/TS format.")
    return channel, timestamp


def fetch_thread(channel: str, timestamp: str) -> dict[str, Any]:
    target, token_error = resolve_bot_token()
    if target is None:
        return {"error": token_error}
    token = target.bot_token
    messages: list[dict[str, object]] = []
    cursor = ""
    seen_cursors: set[str] = set()
    while len(messages) < _MAX_MESSAGES:
        params: dict[str, str | int] = {"channel": channel, "ts": timestamp, "limit": _PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        payload, request_error = _request_json("GET", "conversations.replies", token, params=params)
        if payload is None:
            return {"error": request_error}
        if not payload.get("ok"):
            error = payload.get("error")
            return {"error": str(error or "Slack API request failed.")}
        for item in payload.get("messages") or []:
            if isinstance(item, dict):
                text = redact_slack_token(str(item.get("text", "")), token)
                messages.append(
                    {
                        "user": item.get("user", ""),
                        "text": text,
                        "ts": item.get("ts", ""),
                        "reactions": [
                            reaction.get("name", "")
                            for reaction in item.get("reactions") or []
                            if isinstance(reaction, dict)
                        ],
                    }
                )
                if len(messages) == _MAX_MESSAGES:
                    break
        metadata = payload.get("response_metadata")
        next_cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        cursor = str(next_cursor).strip()
        if not cursor or cursor in seen_cursors:
            # No further page (empty cursor) or a repeated cursor (API loop
            # guard): clear it so `truncated` below reflects a genuine cap hit,
            # not an anomalous stop.
            cursor = ""
            break
        seen_cursors.add(cursor)
    return {
        "channel": channel,
        "ts": timestamp,
        "messages": messages,
        "count": len(messages),
        "truncated": len(messages) == _MAX_MESSAGES and bool(cursor),
    }
