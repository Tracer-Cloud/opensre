"""Telegram delivery helper - posts messages to the Telegram Bot API."""

from __future__ import annotations

import contextlib
import logging
import re
from http import HTTPStatus
from typing import Any

from infrastructure.delivery.notifications.delivery_transport import post_json
from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.delivery.notifications.redaction import redact_token
from infrastructure.text.truncation import truncate

logger = logging.getLogger(__name__)

_MESSAGE_LIMIT = MAX_MESSAGE_SIZE
_BOT_TOKEN_RE = re.compile(r"(bot)[^/]+(/)")
# Every Telegram ``parse_mode=HTML`` element that wraps content, so a cut taken
# while one is open leaves markup the Bot API rejects. Mirrors the supported set
# documented in ``integrations.telegram.markdown``; ``<pre>`` in particular is
# what ``render_markdown_as_telegram_html`` emits for fenced code blocks.
_BALANCED_TAG_NAMES = frozenset({"a", "b", "code", "i", "pre", "s", "u"})


def _strip_trailing_incomplete_tag(chunk: str) -> str:
    """Drop a trailing ``<``… fragment with no closing ``>``."""
    while True:
        last_lt = chunk.rfind("<")
        if last_lt == -1:
            return chunk
        if ">" in chunk[last_lt:]:
            return chunk
        chunk = chunk[:last_lt].rstrip()


def _strip_trailing_partial_entity(chunk: str) -> str:
    """Remove a trailing ``&``… suffix that is not a complete character reference."""
    amp = chunk.rfind("&")
    if amp == -1:
        return chunk
    tail = chunk[amp:]
    if ";" in tail:
        return chunk
    return chunk[:amp].rstrip()


def _parse_tag(raw_tag: str) -> tuple[str, bool]:
    """Return ``(element_name, is_closing)`` for *raw_tag*, angle brackets included.

    The name is lowercased, and empty when the tag carries none. ``<>`` and
    ``< >`` occur in ordinary report text (a SQL ``<>`` predicate, say), so they
    must fall through as plain text rather than being read as an element.
    """
    inner = raw_tag[1:-1].strip()
    is_closing = inner.startswith("/")
    parts = inner.removeprefix("/").split(maxsplit=1)
    return (parts[0].lower() if parts else ""), is_closing


def _balance_telegram_markup_tags(fragment: str) -> str:
    """Append closing tags for Telegram elements still open at the end of *fragment*."""
    stack: list[str] = []
    pos = 0
    while pos < len(fragment):
        if fragment[pos] != "<":
            pos += 1
            continue
        end = fragment.find(">", pos)
        if end == -1:
            break
        name, is_closing = _parse_tag(fragment[pos : end + 1])
        if name in _BALANCED_TAG_NAMES:
            if not is_closing:
                stack.append(name)
            elif stack and stack[-1] == name:
                stack.pop()
        pos = end + 1

    return fragment + "".join(f"</{tag}>" for tag in reversed(stack))


def truncate_for_telegram_html(text: str, max_len: int, suffix: str = "…") -> str:
    """Truncate *text* for Telegram ``parse_mode=HTML`` without breaking markup at the cut."""
    if len(text) <= max_len:
        return text
    if max_len <= len(suffix):
        return suffix[:max_len]
    room = max_len - len(suffix)
    while room > 0:
        chunk = text[:room]
        chunk = _strip_trailing_incomplete_tag(chunk)
        chunk = _strip_trailing_partial_entity(chunk)
        chunk = _balance_telegram_markup_tags(chunk)
        candidate = chunk + suffix
        if len(candidate) <= max_len:
            return candidate
        room -= 1
    return suffix[:max_len]


def _redact_arg(a: object) -> object:
    """Redact bot token from a log arg, preserving the original type if no match."""
    s = str(a)
    redacted = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", s)
    return redacted if redacted != s else a


class _TelegramTokenFilter(logging.Filter):
    """Scrub Telegram bot tokens from httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _BOT_TOKEN_RE.sub(r"\1<redacted>\2", str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        return True


def _install_httpx_token_filter() -> None:
    _filter = _TelegramTokenFilter()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_filter)


_install_httpx_token_filter()


def post_telegram_message(
    chat_id: str,
    text: str,
    bot_token: str,
    parse_mode: str = "",
    reply_to_message_id: str = "",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str, str]:
    """Call Telegram Bot API sendMessage endpoint.

    Returns (success, error, message_id).
    """
    logger.debug("[telegram] post message params chat_id: %s", chat_id)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id and reply_to_message_id != "0":
        with contextlib.suppress(ValueError, TypeError):
            payload["reply_to_message_id"] = int(reply_to_message_id)
    response = post_json(
        url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
        payload=payload,
    )
    if not response.ok:
        error = redact_token(response.error, bot_token)
        logger.warning("[telegram] post message exception: %s", error)
        return False, error, ""
    if response.status_code != HTTPStatus.OK:
        logger.warning("[telegram] post message failed: %s", response.status_code)
        if response.data:
            error_message = str(
                response.data.get("description", response.data.get("error", "unknown"))
            )
        else:
            error_message = response.text or f"HTTP {response.status_code}"
        safe_error = redact_token(error_message, bot_token)
        logger.warning("[telegram] post message failed: %s", safe_error)
        return False, safe_error, ""
    result = response.data.get("result", {})
    message_id = str(result.get("message_id") or "") if isinstance(result, dict) else ""
    return True, "", message_id


def send_telegram_report(
    report: str,
    telegram_ctx: dict[str, Any],
    *,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Send a truncated report to Telegram. Returns (success, error)."""
    bot_token: str = str(telegram_ctx.get("bot_token") or "")
    chat_id: str = str(telegram_ctx.get("chat_id") or "")
    if not bot_token or not chat_id:
        return False, "Missing bot_token or chat_id"
    reply_to_message_id: str = str(telegram_ctx.get("reply_to_message_id") or "")
    if parse_mode.upper() == "HTML":
        text = truncate_for_telegram_html(report, _MESSAGE_LIMIT, suffix="…")
    else:
        text = truncate(report, _MESSAGE_LIMIT, suffix="…")
    post_success, error, _ = post_telegram_message(
        chat_id,
        text,
        bot_token,
        parse_mode=parse_mode,
        reply_to_message_id=reply_to_message_id,
        reply_markup=reply_markup,
    )
    return (True, "") if post_success else (False, error)
