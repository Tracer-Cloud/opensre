"""Scheduled-delivery adapter: post a scheduled task's message to Discord."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.credentials import resolve_discord_credentials
from infrastructure.scheduling.scheduler.delivery import strip_html
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.discord.delivery import send_discord_report


class DiscordScheduledDelivery:
    """Deliver a scheduled task's message via the Discord report helper.

    Posts to the channel directly (no thread_id); the report helper handles
    embed truncation. Discord uses embeds, not HTML, so tags are stripped.
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_discord_credentials(task.params)
        bot_token = creds.get("bot_token", "")
        if not bot_token or not task.chat_id:
            return False, "Missing bot_token or channel_id for Discord", ""

        discord_ctx = {
            "channel_id": task.chat_id,
            "bot_token": bot_token,
        }
        ok, error = send_discord_report(strip_html(message), discord_ctx)
        return ok, error, ""
