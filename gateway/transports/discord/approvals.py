"""Discord button approval prompt for write tools.

The transport-neutral half — broker, button identifiers, harness hooks — lives
in :mod:`gateway.core.middleware.approvals`. This module renders the Discord side:
message components on the prompt, and click routing back to the broker.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import discord

from gateway.core.middleware.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.discord.client import edit_message, send_message_with_components

logger = logging.getLogger("gateway")


class DiscordApprovalPrompter:
    """Posts Approve/Deny buttons in-channel and waits for an authorized click."""

    def __init__(
        self,
        *,
        broker: ApprovalBroker,
        bot_token: str,
        channel_id: str,
    ) -> None:
        self._broker = broker
        self._bot_token = bot_token
        self._channel_id = channel_id

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        approval_id = self._broker.create(
            platform="discord",
            chat_id=self._channel_id,
        )
        preview = arguments_preview(arguments)
        body = f"**Approval needed — `{tool_name}`**"
        if reason.strip():
            body += f"\n{reason.strip()}"
        if preview:
            body += f"\n```\n{preview}\n```"
        components = _approval_components(approval_id)
        message_id = send_message_with_components(
            channel_id=self._channel_id,
            content=body[:2000],
            components=components,
            bot_token=self._bot_token,
        )
        if message_id is None:
            logger.warning(
                "[discord-gateway] approval prompt post failed tool=%s channel=%s",
                tool_name,
                self._channel_id,
            )
            return (False, "")
        timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
        approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        outcome = _outcome_text(tool_name, approved=approved, decided_by=decided_by)
        edit_message(
            channel_id=self._channel_id,
            message_id=message_id,
            content=outcome,
            bot_token=self._bot_token,
        )
        return (approved, decided_by)


def handle_component_interaction(
    interaction: discord.Interaction,
    *,
    broker: ApprovalBroker,
    allowed_user_ids: list[str],
) -> bool:
    """Resolve Approve/Deny clicks.

    Write-tool approvals always require an explicit allowlist — open-guild chat
    trust must not extend to approving side-effecting tools.
    """
    data = interaction.data
    if not isinstance(data, dict):
        return False
    custom_id = str(data.get("custom_id") or "")
    if custom_id.startswith(f"{APPROVE_ACTION_ID}:"):
        approval_id = custom_id[len(APPROVE_ACTION_ID) + 1 :]
        approved = True
    elif custom_id.startswith(f"{DENY_ACTION_ID}:"):
        approval_id = custom_id[len(DENY_ACTION_ID) + 1 :]
        approved = False
    else:
        return False
    user_id = str(interaction.user.id)
    allowed_user_ids_set = set(allowed_user_ids)
    if not allowed_user_ids_set or user_id not in allowed_user_ids_set:
        logger.info("[discord-gateway] approval click from unauthorized user=%s ignored", user_id)
        return False
    return broker.resolve(approval_id, approved=approved, decided_by=user_id)


def _approval_components(approval_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 3,
                    "label": "Approve",
                    "custom_id": f"{APPROVE_ACTION_ID}:{approval_id}",
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "Deny",
                    "custom_id": f"{DENY_ACTION_ID}:{approval_id}",
                },
            ],
        }
    ]


def _outcome_text(tool_name: str, *, approved: bool, decided_by: str) -> str:
    if approved:
        return f"✅ `{tool_name}` approved by <@{decided_by}>"
    if decided_by:
        return f"🚫 `{tool_name}` denied by <@{decided_by}>"
    return f"⏱ Approval request for `{tool_name}` expired — action skipped."


__all__ = [
    "DiscordApprovalPrompter",
    "handle_component_interaction",
]
