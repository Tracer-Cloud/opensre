"""Shared Slack Web API helpers for bot-token tools (read / roster / reply).

Transport for agent Slack tools lives here (not in ``tools/slack_*``) so
multiple tools share one client, charset headers, and error hints.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.slack import (
    SLACK_ACCESS_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_USER_TOKEN_PREFIXES,
)
from config.llm_credentials import resolve_env_credential

_API_BASE = "https://slack.com/api"
_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_REQUEST_ATTEMPTS = 3
_DEFAULT_RETRY_WAIT_SECONDS = 0.5
_MAX_RETRY_WAIT_SECONDS = 5.0
_PAGE_LIMIT = 200
_MAX_MEMBER_PAGES = 5
_MAX_CHANNEL_LIST_PAGES = 10
# Thread reads: paginate replies to the last page (bounded) for recent replies.
_MAX_THREAD_PAGES = 25
_MAX_TEXT_CHARS_PER_MESSAGE = 2_000
_MAX_LIST_DISCOVERY_PAGES = 5
_MAX_LIST_ITEM_PAGES = 5
_MAX_LIST_ITEMS = 100
# A Slack List id is a file id: the letter "F" then 5+ alphanumerics (e.g. "F0123ABCD").
_SLACK_LIST_ID_RE = re.compile(r"^F[A-Z0-9]{5,}$")

# Slack channel/DM/group IDs look like C0123ABCD — not bare names like "devs".
_CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]{8,}$")


@dataclass(frozen=True)
class SlackBotTarget:
    """Resolved Slack bot credentials.

    ``bot_token`` is excluded from repr so traces/assertions do not leak it.
    """

    bot_token: str

    def __repr__(self) -> str:
        return "SlackBotTarget(bot_token=<redacted>)"


def resolve_bot_token() -> tuple[SlackBotTarget | None, str]:
    """Resolve the Slack bot token from env/keyring first, then the integration store.

    ``resolve_env_credential`` checks process env then the OS keyring (wizard
    ``sync_env_secret``), preserving the historical env-before-store order.
    """
    env_token = resolve_env_credential(SLACK_BOT_TOKEN_ENV).strip()
    if env_token:
        return SlackBotTarget(bot_token=env_token), ""

    try:
        from integrations.catalog import resolve_effective_integrations

        slack_integration = resolve_effective_integrations().get("slack") or {}
        config = slack_integration.get("config") if isinstance(slack_integration, dict) else {}
        stored_token = str(config.get("bot_token", "") if isinstance(config, dict) else "").strip()
    except Exception as exc:
        return None, str(exc)

    if not stored_token:
        return None, (
            "Slack bot token is not configured. Set SLACK_BOT_TOKEN or configure "
            "the Slack integration."
        )
    return SlackBotTarget(bot_token=stored_token), ""


def _bot_token_from_slack_source(slack: Any) -> str:
    """Read ``bot_token`` from a flat or ``{config: …}`` Slack source dict."""
    if not isinstance(slack, dict):
        return ""
    direct = str(slack.get("bot_token") or "").strip()
    if direct:
        return direct
    nested = slack.get("config")
    if isinstance(nested, dict):
        return str(nested.get("bot_token") or "").strip()
    return ""


def bot_token_configured(sources: dict[str, Any] | None = None) -> bool:
    """True when env, tool ``sources``, or the local store expose a bot token.

    Availability must not depend solely on the turn's resolved-integration map:
    gateway sessions can carry an empty or metadata-only cache (``CONNECTED
    INTEGRATIONS: none``) while ``SLACK_BOT_TOKEN`` / the store still have a
    usable token. ``resolve_bot_token`` already understands that shape.
    """
    if resolve_env_credential(SLACK_BOT_TOKEN_ENV).strip():
        return True
    if _bot_token_from_slack_source((sources or {}).get("slack")):
        return True
    target, _error = resolve_bot_token()
    return target is not None


_SEARCH_NEEDS_USER_TOKEN = (
    "Slack search needs a user token: search.messages rejects bot tokens "
    f"(not_allowed_token_type). Set {SLACK_ACCESS_TOKEN_ENV} to a user token "
    "(xoxp-…) holding search:read, or read a known channel with "
    "slack_read_messages instead."
)


def is_user_token(token: str) -> bool:
    """True when *token* is a Slack user token (``xoxp-``, or rotating ``xoxe.xoxp-``)."""
    return token.strip().startswith(SLACK_USER_TOKEN_PREFIXES)


def resolve_user_token() -> tuple[SlackBotTarget | None, str]:
    """Resolve a Slack **user** token for ``search.messages``.

    ``SLACK_ACCESS_TOKEN`` first, then the bot-token path when the value found
    there is itself a user token (a workspace may have only ever configured one
    Slack credential). A bot token is never returned: Slack refuses it for
    search, so returning one would guarantee a failed call.
    """
    env_token = resolve_env_credential(SLACK_ACCESS_TOKEN_ENV).strip()
    if is_user_token(env_token):
        return SlackBotTarget(bot_token=env_token), ""

    target, _error = resolve_bot_token()
    if target is not None and is_user_token(target.bot_token):
        return target, ""
    return None, _SEARCH_NEEDS_USER_TOKEN


def user_token_configured() -> bool:
    """True when a Slack user token is available, so search can actually run."""
    target, _error = resolve_user_token()
    return target is not None


def _bearer_headers(token: str) -> dict[str, str]:
    # Match delivery.py: Slack wants charset=utf-8 on JSON bodies.
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _api_error_hint(error: str, *, context: str) -> str:
    hints = {
        "not_in_channel": "The bot is not in this channel — /invite it first.",
        "channel_not_found": "No channel with this ID is visible to the bot.",
        "missing_scope": {
            "history": (
                "The Slack app lacks a history scope "
                "(channels:history / groups:history / im:history / mpim:history). "
                "Add it and reinstall the app."
            ),
            "users": ("The Slack app lacks the users:read scope. Add it and reinstall the app."),
            "list": (
                "The Slack app lacks a channel list scope "
                "(channels:read / groups:read / im:read / mpim:read). "
                "Add it and reinstall the app."
            ),
            "post": ("The Slack app lacks chat:write. Add it and reinstall the app."),
            "join": (
                "The Slack app lacks channels:join (public) or needs an invite for private "
                "channels. Add scopes / invite the bot and reinstall if needed."
            ),
            "search": ("The Slack app lacks search:read. Add it and reinstall the app."),
            "reactions": ("The Slack app lacks reactions:write. Add it and reinstall the app."),
            "files": (
                "The Slack app lacks the files:read scope (needed to discover Slack "
                "Lists / download attachments). Add it and reinstall the app."
            ),
            "lists": (
                "The Slack app lacks the lists:read scope (needed to read Slack List "
                "rows). Add it and reinstall the app."
            ),
        }.get(
            context,
            "The Slack app is missing a required OAuth scope. Reinstall after adding scopes.",
        ),
        "thread_not_found": "No thread with this parent ts was found in the channel.",
        "not_allowed_token_type": _SEARCH_NEEDS_USER_TOKEN,
    }
    return hints.get(error, f"Slack API error: {error}")


_client_lock = threading.Lock()
_client: httpx.Client | None = None


def _shared_client() -> httpx.Client:
    """Return a process-wide keep-alive client so calls reuse one connection."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                base_url=_API_BASE,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return _client


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "")
    try:
        return min(float(raw), _MAX_RETRY_WAIT_SECONDS)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_WAIT_SECONDS


def _request_json(
    method: str,
    path: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    idempotent: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    """Call one Slack Web API method, retrying transient failures.

    Returns ``(payload, "")`` on any HTTP-level success (Slack encodes its own
    errors in ``payload["ok"]`` for callers to classify), or ``(None, detail)``
    with a specific reason — rate-limit, timeout, HTTP status, or bad payload —
    so callers can surface an actionable hint instead of a generic failure.

    Set ``idempotent=False`` for writes whose repeat would be visible (e.g.
    ``chat.postMessage``). A timeout or 5xx means the outcome is unknown — Slack
    has no idempotency key, so such a request is not retried and the failure is
    returned for the caller to decide. Rate-limit (429) is always retried: Slack
    rejected the call without applying it, so a retry cannot duplicate.
    """
    client = _shared_client()
    headers = {"Authorization": f"Bearer {token}"}
    if method != "GET":
        headers = _bearer_headers(token)

    last_error = f"Slack {path} request failed."
    for attempt in range(_MAX_REQUEST_ATTEMPTS):
        try:
            if method == "GET":
                response = client.get(f"/{path}", headers=headers, params=params or {})
            else:
                response = client.post(f"/{path}", headers=headers, json=json_body or {})
        except httpx.TimeoutException:
            last_error = f"Slack {path} timed out."
        except httpx.HTTPError:
            last_error = f"Slack {path} request failed."
        else:
            status = response.status_code
            if status == HTTPStatus.TOO_MANY_REQUESTS:
                last_error = f"Slack {path} is rate-limited; try again shortly."
                if attempt < _MAX_REQUEST_ATTEMPTS - 1:
                    time.sleep(_retry_after_seconds(response))
                    continue
                return None, last_error
            if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                last_error = f"Slack {path} returned HTTP {status}."
            elif status >= HTTPStatus.BAD_REQUEST:
                return None, f"Slack {path} returned HTTP {status}."
            else:
                try:
                    payload = response.json()
                except ValueError:
                    return None, f"Slack {path} returned a non-JSON payload."
                if not isinstance(payload, dict):
                    return None, f"Slack {path} returned an unexpected payload."
                return payload, ""
        if not idempotent:
            return None, last_error
        if attempt < _MAX_REQUEST_ATTEMPTS - 1:
            time.sleep(_DEFAULT_RETRY_WAIT_SECONDS)
    return None, last_error


def normalize_channel_ref(channel: str) -> tuple[bool, str, str]:
    """Return ``(ok, normalized_ref, error)``.

    Accepts Slack IDs (``C…`` / ``D…`` / ``G…``) or ``#channel-name`` / bare names.
    """
    normalized = str(channel or "").strip()
    if not normalized:
        return False, "", "channel cannot be empty."
    if _CHANNEL_ID_RE.match(normalized):
        return True, normalized, ""
    name = normalized[1:] if normalized.startswith("#") else normalized
    name = name.strip()
    if not name or " " in name:
        return (
            False,
            "",
            "channel must be a Slack ID (C…/D…/G…) or a #channel-name.",
        )
    return True, f"#{name}", ""


def resolve_channel_id(target: SlackBotTarget, channel_ref: str) -> tuple[str | None, str]:
    """Resolve ``C…`` or ``#name`` to a channel ID the bot can see."""
    ok, normalized, err = normalize_channel_ref(channel_ref)
    if not ok:
        return None, err
    if not normalized.startswith("#"):
        return normalized, ""

    want = normalized[1:].lower()
    cursor = ""
    for _ in range(_MAX_CHANNEL_LIST_PAGES):
        params: dict[str, Any] = {
            "limit": _PAGE_LIMIT,
            "types": "public_channel,private_channel,mpim,im",
            "exclude_archived": True,
        }
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json(
            "GET", "conversations.list", target.bot_token, params=params
        )
        if payload is None:
            return None, req_err
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="list")

        for raw in payload.get("channels") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").lower()
            if name == want:
                channel_id = str(raw.get("id") or "").strip()
                if channel_id:
                    return channel_id, ""

        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            break

    return None, f"No channel named #{want} is visible to the bot — /invite it or use a C… ID."


def _normalize_message(raw: dict[str, Any]) -> dict[str, str]:
    text = str(raw.get("text") or "")
    if len(text) > _MAX_TEXT_CHARS_PER_MESSAGE:
        text = text[: _MAX_TEXT_CHARS_PER_MESSAGE - 1].rstrip() + "…"
    return {
        "user": str(raw.get("user") or raw.get("bot_id") or "unknown"),
        "ts": str(raw.get("ts") or ""),
        "thread_ts": str(raw.get("thread_ts") or ""),
        "text": text,
    }


def fetch_channel_messages(
    target: SlackBotTarget,
    *,
    channel_id: str,
    limit: int,
    thread_ts: str = "",
) -> tuple[list[dict[str, str]] | None, str]:
    """Fetch the most recent channel messages or thread replies, oldest first."""
    parent = str(thread_ts or "").strip()
    if parent:
        return _fetch_thread_replies(target, channel_id=channel_id, parent=parent, limit=limit)

    payload, req_err = _request_json(
        "GET",
        "conversations.history",
        target.bot_token,
        params={"channel": channel_id, "limit": limit},
    )
    if payload is None:
        return None, req_err
    if not payload.get("ok"):
        return None, _api_error_hint(
            str(payload.get("error") or "unknown_error"), context="history"
        )

    messages = [
        _normalize_message(m) for m in (payload.get("messages") or []) if isinstance(m, dict)
    ]
    messages.reverse()  # history is newest-first; present oldest-first
    return messages, ""


def _fetch_thread_replies(
    target: SlackBotTarget, *, channel_id: str, parent: str, limit: int
) -> tuple[list[dict[str, str]] | None, str]:
    """Return the newest ``limit`` thread replies, oldest-first.

    conversations.replies is oldest-first with no reverse paging, so follow the
    cursor to the true end. If a thread is longer than the safety bound can
    traverse, return an error rather than stale middle replies — never present
    old content as current.
    """
    collected: list[dict[str, str]] = []
    cursor = ""
    for _ in range(_MAX_THREAD_PAGES):
        params: dict[str, Any] = {"channel": channel_id, "ts": parent, "limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json(
            "GET", "conversations.replies", target.bot_token, params=params
        )
        if payload is None:
            return None, req_err
        if not payload.get("ok"):
            return None, _api_error_hint(
                str(payload.get("error") or "unknown_error"), context="history"
            )
        collected.extend(
            _normalize_message(m) for m in (payload.get("messages") or []) if isinstance(m, dict)
        )
        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            return collected[-limit:], ""
    return None, (
        "This thread is too long to read the most recent replies reliably. "
        "Ask about a narrower window or the parent message directly."
    )


def fetch_team_members(
    target: SlackBotTarget,
) -> tuple[list[dict[str, Any]] | None, str, bool]:
    """Fetch workspace members. Returns ``(members, error, truncated)``."""
    members: list[dict[str, Any]] = []
    cursor = ""
    truncated = False
    for page in range(_MAX_MEMBER_PAGES):
        params: dict[str, Any] = {"limit": _PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        payload, req_err = _request_json("GET", "users.list", target.bot_token, params=params)
        if payload is None:
            return None, req_err, False
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="users"), False

        for raw in payload.get("members") or []:
            if not isinstance(raw, dict) or raw.get("deleted"):
                continue
            profile = raw.get("profile") or {}
            username = str(raw.get("name") or "")
            if username == "slackbot":
                continue
            members.append(
                {
                    "id": str(raw.get("id") or ""),
                    "username": username,
                    "real_name": str(profile.get("real_name") or ""),
                    "display_name": str(profile.get("display_name") or ""),
                    "title": str(profile.get("title") or ""),
                    "is_bot": bool(raw.get("is_bot")),
                }
            )

        cursor = str(((payload.get("response_metadata") or {}).get("next_cursor")) or "")
        if not cursor:
            break
        if page == _MAX_MEMBER_PAGES - 1 and cursor:
            truncated = True

    return members, "", truncated


def post_channel_message(
    target: SlackBotTarget,
    *,
    channel_id: str,
    text: str,
    thread_ts: str = "",
) -> tuple[bool, str]:
    """Post plain text to a channel (optionally as a thread reply)."""
    payload, error = _post_channel_message_payload(
        target, channel_id=channel_id, text=text, thread_ts=thread_ts
    )
    return payload is not None, error


def post_channel_message_with_id(
    target: SlackBotTarget,
    *,
    channel_id: str,
    text: str,
    thread_ts: str = "",
) -> tuple[str | None, str]:
    """Post plain text and return Slack's message timestamp identifier."""
    payload, error = _post_channel_message_payload(
        target, channel_id=channel_id, text=text, thread_ts=thread_ts
    )
    if payload is None:
        return None, error
    message_ts = str(payload.get("ts") or "").strip()
    if not message_ts:
        return None, "Slack accepted the message without returning its timestamp."
    return message_ts, ""


def _post_channel_message_payload(
    target: SlackBotTarget,
    *,
    channel_id: str,
    text: str,
    thread_ts: str,
) -> tuple[dict[str, Any] | None, str]:
    body: dict[str, str] = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    payload, req_err = _request_json(
        "POST", "chat.postMessage", target.bot_token, json_body=body, idempotent=False
    )
    if payload is None:
        return None, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        return None, _api_error_hint(error, context="post")
    return payload, ""


def join_channel(target: SlackBotTarget, *, channel_id: str) -> tuple[bool, str]:
    """Join a public channel the bot can see (``conversations.join``)."""
    payload, req_err = _request_json(
        "POST",
        "conversations.join",
        target.bot_token,
        json_body={"channel": channel_id},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error == "already_in_channel":
            return True, ""
        if error == "method_not_supported_for_channel_type":
            return False, (
                "conversations.join only works on public channels. For a private "
                "channel, DM, or group DM, invite the bot instead."
            )
        return False, _api_error_hint(error, context="join")
    return True, ""


def find_slack_lists(
    target: SlackBotTarget,
    *,
    name_query: str = "",
    limit: int = 20,
) -> tuple[list[dict[str, str]] | None, str]:
    """Discover Slack Lists visible to the bot (``files.list``, filetype=list).

    Slack Lists are file objects (``F…`` ids). There is no dedicated
    ``lists.list`` method; discovery uses ``files.list`` + client-side filter.
    Optional ``name_query`` keeps case-insensitive substring matches on
    ``name`` / ``title``.
    """
    needle = str(name_query or "").strip().lower()
    want = max(1, min(int(limit), 50))
    found: list[dict[str, str]] = []
    page = 1
    for _ in range(_MAX_LIST_DISCOVERY_PAGES):
        payload, req_err = _request_json(
            "GET",
            "files.list",
            target.bot_token,
            params={"count": 100, "page": page, "types": "all"},
        )
        if payload is None:
            return None, req_err
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="files")

        for raw in payload.get("files") or []:
            if not isinstance(raw, dict):
                continue
            filetype = str(raw.get("filetype") or "").lower()
            mimetype = str(raw.get("mimetype") or "").lower()
            if filetype != "list" and "slack-list" not in mimetype:
                continue
            list_id = str(raw.get("id") or "").strip()
            if not list_id:
                continue
            title = str(raw.get("title") or raw.get("name") or list_id)
            name = str(raw.get("name") or title)
            if needle and needle not in title.lower() and needle not in name.lower():
                continue
            found.append(
                {
                    "list_id": list_id,
                    "name": name,
                    "title": title,
                    "permalink": str(raw.get("permalink") or ""),
                }
            )
            if len(found) >= want:
                return found, ""

        raw_paging = payload.get("paging")
        paging = raw_paging if isinstance(raw_paging, dict) else {}
        pages = int(paging.get("pages") or 1)
        if page >= pages:
            break
        page += 1
    return found, ""


def fetch_slack_list_items(
    target: SlackBotTarget,
    *,
    list_id: str,
    limit: int = 50,
    include_archived: bool = False,
) -> tuple[list[dict[str, Any]] | None, str, bool]:
    """Read rows from a Slack List (``slackLists.items.list``).

    Returns ``(rows, error, truncated)``. ``truncated`` is true when the list
    has more rows beyond the safety bound, so the caller does not present a
    partial read as the whole list.
    """
    lid = str(list_id or "").strip().upper()
    if not lid:
        return None, "list_id cannot be empty.", False
    if not _SLACK_LIST_ID_RE.match(lid):
        return None, "list_id must be a Slack List id (F…).", False

    want = max(1, min(int(limit), _MAX_LIST_ITEMS))
    items: list[dict[str, Any]] = []
    cursor = ""
    truncated = False
    for page in range(_MAX_LIST_ITEM_PAGES):
        body: dict[str, Any] = {"list_id": lid, "limit": min(want - len(items), 100)}
        if cursor:
            body["cursor"] = cursor
        if include_archived:
            body["archived"] = True
        payload, req_err = _request_json(
            "POST",
            "slackLists.items.list",
            target.bot_token,
            json_body=body,
        )
        if payload is None:
            return None, req_err, False
        if not payload.get("ok"):
            error = str(payload.get("error") or "unknown_error")
            return None, _api_error_hint(error, context="lists"), False

        for raw in payload.get("items") or []:
            if not isinstance(raw, dict):
                continue
            items.append(_normalize_list_item(raw))
            if len(items) >= want:
                return items, "", False

        meta = payload.get("response_metadata")
        next_cursor = ""
        if isinstance(meta, dict):
            next_cursor = str(meta.get("next_cursor") or "").strip()
        if not next_cursor:
            break
        cursor = next_cursor
        if page == _MAX_LIST_ITEM_PAGES - 1:
            truncated = True
    return items, "", truncated


def _normalize_list_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a List row into a stable agent-facing dict."""
    fields_out: dict[str, Any] = {}
    name = ""
    assignees: list[str] = []
    status = ""
    due_date = ""
    for field in raw.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or field.get("column_id") or "").strip()
        if not key:
            continue
        text = str(field.get("text") or "").strip()
        users = [str(u) for u in (field.get("user") or []) if u]
        dates = [str(d) for d in (field.get("date") or []) if d]
        selects = [str(s) for s in (field.get("select") or []) if s]
        value: Any = text
        if users:
            value = users
        elif dates:
            value = dates[0] if len(dates) == 1 else dates
        elif selects:
            value = selects[0] if len(selects) == 1 else selects
        elif field.get("number"):
            nums = field.get("number") or []
            value = nums[0] if isinstance(nums, list) and len(nums) == 1 else nums
        elif field.get("checkbox") is not None:
            boxes = field.get("checkbox") or []
            value = boxes[0] if isinstance(boxes, list) and boxes else field.get("value")
        elif "value" in field and not text:
            value = field.get("value")
        fields_out[key] = value

        key_l = key.lower()
        if (
            text
            and not name
            and (
                key_l in ("name", "title", "task", "rich_text_notes", "todo_text")
                or "name" in key_l
                or key_l.endswith("_notes")
            )
        ):
            name = text
        if users and (key_l in ("owner", "assignee", "assignees") or "assign" in key_l):
            assignees.extend(users)
        if selects and not status and (key_l == "status" or "status" in key_l):
            status = selects[0]
        if dates and not due_date and (key_l in ("date", "due", "due_date") or "due" in key_l):
            due_date = dates[0]

    if not name:
        # Fall back to the first non-empty text field.
        for value in fields_out.values():
            if isinstance(value, str) and value.strip():
                name = value.strip()
                break

    return {
        "id": str(raw.get("id") or ""),
        "list_id": str(raw.get("list_id") or ""),
        "name": name,
        "assignees": list(dict.fromkeys(assignees)),
        "status": status,
        "due_date": due_date,
        "archived": bool(raw.get("archived")),
        "fields": fields_out,
    }


def search_messages(
    target: SlackBotTarget,
    *,
    query: str,
    count: int = 20,
) -> tuple[list[dict[str, str]] | None, str, bool]:
    """Search workspace messages. Returns ``(matches, error, truncated)``.

    ``search.messages`` is fetched as a single page, so ``truncated`` is true
    when the hit count reaches the requested cap and more matches may exist.
    """
    q = str(query or "").strip()
    if not q:
        return None, "query cannot be empty.", False
    if not is_user_token(target.bot_token):
        return None, _SEARCH_NEEDS_USER_TOKEN, False
    limit = max(1, min(int(count), 100))
    payload, req_err = _request_json(
        "GET",
        "search.messages",
        target.bot_token,
        params={"query": q, "count": limit, "sort": "timestamp"},
    )
    if payload is None:
        return None, req_err, False
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        return None, _api_error_hint(error, context="search"), False

    matches = ((payload.get("messages") or {}).get("matches")) or []
    results: list[dict[str, str]] = []
    for raw in matches:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        if len(text) > _MAX_TEXT_CHARS_PER_MESSAGE:
            text = text[: _MAX_TEXT_CHARS_PER_MESSAGE - 1].rstrip() + "…"
        channel = raw.get("channel") or {}
        channel_id = ""
        if isinstance(channel, dict):
            channel_id = str(channel.get("id") or "")
        results.append(
            {
                "channel_id": channel_id,
                "user": str(raw.get("user") or ""),
                "ts": str(raw.get("ts") or ""),
                "text": text,
                "permalink": str(raw.get("permalink") or ""),
            }
        )
    return results, "", len(results) >= limit


def add_reaction(
    target: SlackBotTarget,
    *,
    channel_id: str,
    timestamp: str,
    emoji: str,
) -> tuple[bool, str]:
    """Add an emoji reaction to a message (``reactions.add``)."""
    name = str(emoji or "").strip().strip(":")
    if not name:
        return False, "emoji cannot be empty."
    payload, req_err = _request_json(
        "POST",
        "reactions.add",
        target.bot_token,
        json_body={"channel": channel_id, "timestamp": timestamp, "name": name},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error in ("already_reacted", "no_reaction"):
            return True, ""
        return False, _api_error_hint(error, context="reactions")
    return True, ""


def remove_reaction(
    target: SlackBotTarget,
    *,
    channel_id: str,
    timestamp: str,
    emoji: str,
) -> tuple[bool, str]:
    """Remove an emoji reaction from a message (``reactions.remove``)."""
    name = str(emoji or "").strip().strip(":")
    if not name:
        return False, "emoji cannot be empty."
    payload, req_err = _request_json(
        "POST",
        "reactions.remove",
        target.bot_token,
        json_body={"channel": channel_id, "timestamp": timestamp, "name": name},
    )
    if payload is None:
        return False, req_err
    if not payload.get("ok"):
        error = str(payload.get("error") or "unknown_error")
        if error in ("no_reaction", "already_reacted"):
            return True, ""
        return False, _api_error_hint(error, context="reactions")
    return True, ""
