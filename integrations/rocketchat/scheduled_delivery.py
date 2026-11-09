"""Scheduled-delivery adapter: post a scheduled task's message to Rocket.Chat."""

from __future__ import annotations

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.scheduling.scheduler.credentials import resolve_rocketchat_credentials
from infrastructure.scheduling.scheduler.delivery import strip_html
from infrastructure.scheduling.scheduler.types import ScheduledTask
from infrastructure.text.truncation import truncate
from integrations.rocketchat.delivery import post_rocketchat_message


class RocketChatScheduledDelivery:
    """Deliver a scheduled task's message via Rocket.Chat's REST API.

    Requires token credentials (server_url + auth_token + user_id): scheduled
    tasks carry an explicit ``chat_id``, which an incoming webhook's fixed
    destination cannot honor, so webhook mode is deliberately refused (same rule
    as the rocketchat_send_message tool). Rocket.Chat uses Markdown, not HTML.
    """

    def deliver(self, task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        creds = resolve_rocketchat_credentials(task.params)
        server_url = creds.get("server_url", "")
        auth_token = creds.get("auth_token", "")
        user_id = creds.get("user_id", "")
        if not (server_url and auth_token and user_id):
            if creds.get("webhook_url"):
                return (
                    False,
                    "Rocket.Chat scheduled delivery targets an explicit channel, which "
                    "needs token credentials (server_url, auth_token, user_id); the "
                    "incoming webhook's destination is fixed and cannot honor chat_id",
                    "",
                )
            return False, "Missing server_url, auth_token, or user_id for Rocket.Chat", ""
        if not task.chat_id:
            return False, "Missing chat_id (channel) for Rocket.Chat", ""

        plain_message = truncate(strip_html(message), MAX_MESSAGE_SIZE, suffix="…")
        ok, error, msg_id = post_rocketchat_message(
            server_url,
            task.chat_id,
            plain_message,
            auth_token,
            user_id,
        )
        return (True, "", msg_id) if ok else (False, error, "")
