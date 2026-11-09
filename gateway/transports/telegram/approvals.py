"""Inline-keyboard approval prompt for write tools on Telegram.

The transport-neutral half — broker, button identifiers, harness hooks — lives
in :mod:`gateway.core.middleware.approvals`. This module renders the Telegram side:
an Approve / Deny inline keyboard, and callback_query routing back to the broker.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from gateway.core.middleware.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.telegram.poller.client import TelegramBotClient
from gateway.transports.telegram.settings import TelegramCallbackQuery

logger = logging.getLogger("gateway")


class TelegramApprovalPrompter:
    """Posts Approve/Deny buttons in-chat and waits for an authorized click."""

    def __init__(
        self,
        *,
        client: TelegramBotClient,
        broker: ApprovalBroker,
        chat_id: str,
    ) -> None:
        self._client = client
        self._broker = broker
        self._chat_id = chat_id

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        approval_id = self._broker.create(
            platform="telegram",
            chat_id=self._chat_id,
        )
        body = f"🔒 Approval needed — `{tool_name}`"
        if reason.strip():
            body += f"\n{reason.strip()}"
        preview = arguments_preview(arguments)
        if preview:
            body += f"\n```\n{preview}\n```"
        ok, error, message_id = self._client.send_message(
            self._chat_id,
            body,
            reply_markup=_approval_keyboard(approval_id),
        )
        if not ok or not message_id:
            logger.warning(
                "[telegram-gateway] approval prompt post failed tool=%s chat=%s err=%s",
                tool_name,
                self._chat_id,
                error,
            )
            return (False, "")
        timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
        approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        outcome = _outcome_text(tool_name, approved=approved, decided_by=decided_by)
        # Clear buttons so a late click cannot re-fire.
        self._client.edit_message_text(
            self._chat_id,
            message_id,
            outcome,
            reply_markup={"inline_keyboard": []},
        )
        logger.info(
            "[telegram-gateway] approval tool=%s approved=%s decided_by=%s",
            tool_name,
            approved,
            decided_by or "(expired)",
        )
        return (approved, decided_by)


def handle_callback_query(
    event: TelegramCallbackQuery,
    *,
    broker: ApprovalBroker,
    client: TelegramBotClient,
    allowed_user_ids: Sequence[str],
) -> bool:
    """Resolve Approve/Deny callback clicks.

    Write-tool approvals always require an explicit allowlist — open chat trust
    must not extend to approving side-effecting tools.
    """
    data = event.data
    if data.startswith(f"{APPROVE_ACTION_ID}:"):
        approval_id = data[len(APPROVE_ACTION_ID) + 1 :]
        approved = True
    elif data.startswith(f"{DENY_ACTION_ID}:"):
        approval_id = data[len(DENY_ACTION_ID) + 1 :]
        approved = False
    else:
        return False

    allowed = set(allowed_user_ids)
    if not allowed or event.user_id not in allowed:
        logger.info(
            "[telegram-gateway] approval click from unauthorized user=%s ignored",
            event.user_id,
        )
        client.answer_callback_query(event.callback_query_id, text="Not authorized")
        return False

    resolved = broker.resolve(
        approval_id,
        approved=approved,
        decided_by=event.user_id,
    )
    client.answer_callback_query(
        event.callback_query_id,
        text="Approved" if approved else "Denied",
    )
    return resolved


_TELEGRAM_CALLBACK_DATA_LIMIT = 64


def _approval_keyboard(approval_id: str) -> dict[str, Any]:
    approve = f"{APPROVE_ACTION_ID}:{approval_id}"
    deny = f"{DENY_ACTION_ID}:{approval_id}"
    # Telegram Bot API rejects callback_data longer than 64 bytes.
    if (
        len(approve.encode("utf-8")) > _TELEGRAM_CALLBACK_DATA_LIMIT
        or len(deny.encode("utf-8")) > _TELEGRAM_CALLBACK_DATA_LIMIT
    ):
        raise ValueError(
            f"Telegram callback_data exceeds {_TELEGRAM_CALLBACK_DATA_LIMIT} bytes "
            f"(approve={len(approve.encode('utf-8'))}, deny={len(deny.encode('utf-8'))})"
        )
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": approve},
                {"text": "Deny", "callback_data": deny},
            ]
        ]
    }


def _outcome_text(tool_name: str, *, approved: bool, decided_by: str) -> str:
    who = f"by user {decided_by}" if decided_by else "(expired — no click)"
    verb = "approved" if approved else "denied"
    return f"🔒 `{tool_name}` {verb} {who}."


__all__ = [
    "TelegramApprovalPrompter",
    "handle_callback_query",
]
