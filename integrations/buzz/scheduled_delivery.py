"""Scheduled-delivery adapter: post a scheduled task's message to Buzz."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.credentials import resolve_buzz_credentials
from infrastructure.scheduling.scheduler.types import ScheduledTask
from integrations.buzz.delivery import post_buzz_message


class BuzzScheduledDelivery:
    """Deliver a scheduled task's message to its channel or configured default."""

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_buzz_credentials(task.params)
        private_key = creds.get("private_key", "")
        channel = task.chat_id or creds.get("default_channel", "")
        if not private_key or not channel:
            return False, "Missing private_key or chat_id for Buzz", ""

        ok, error, message_id = post_buzz_message(
            creds.get("relay_url", ""),
            channel,
            message,
            private_key,
            auth_tag=creds.get("auth_tag", ""),
            buzz_path=creds.get("buzz_path", ""),
        )
        return (True, "", message_id) if ok else (False, error, "")
