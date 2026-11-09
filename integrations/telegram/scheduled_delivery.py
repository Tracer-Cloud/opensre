"""Scheduled-delivery adapter: post a scheduled task's message to Telegram."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.credentials import resolve_telegram_credentials
from infrastructure.scheduling.scheduler.types import ScheduledTask, TaskKind
from integrations.telegram.delivery import post_telegram_message, truncate_for_telegram_html
from integrations.telegram.formatting import markdown_to_telegram_html
from integrations.telegram.report_format import telegram_report_html

#: Kinds whose message is a model-written report, laid out as one.
_REPORT_KINDS = frozenset({TaskKind.MANUAL_LOOP, TaskKind.RECURRING_SKILL})


class TelegramScheduledDelivery:
    """Deliver a scheduled task's message via the Telegram Bot API.

    Renders a report as Telegram HTML (other messages as plain Markdown), truncates to the 4096-char limit, and
    posts a new top-level message (no reply_to).
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_telegram_credentials(task.params)
        bot_token = creds.get("bot_token", "")
        if not bot_token or not task.chat_id:
            return False, "Missing bot_token or chat_id for Telegram", ""

        if task.kind in _REPORT_KINDS:
            html_message = telegram_report_html(message, loop_name=task.name)
        else:
            html_message = markdown_to_telegram_html(message)
        truncated = truncate_for_telegram_html(html_message, 4096, suffix="…")
        ok, error, msg_id = post_telegram_message(
            task.chat_id, truncated, bot_token, parse_mode="HTML", link_previews=False
        )
        return (True, "", msg_id) if ok else (False, error, "")
