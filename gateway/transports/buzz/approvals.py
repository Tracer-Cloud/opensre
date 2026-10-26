"""Buzz text-reply approval prompt for write tools.

Buzz has no interactive buttons. The mention-poll loop that triggers new
turns (:mod:`gateway.transports.buzz.background`) also recognizes a reply to
a pending approval prompt — via ``pending_approvals``, shared through
:class:`gateway.transports.buzz.runtime.BuzzPollingRuntime` — and resolves it
directly instead of starting a new turn. A plain "approve"/"deny" reply is
enough: Buzz Desktop auto-tags the prompt's author (this agent) on any reply
to it, so it still surfaces in the mention feed with no explicit @mention.

The prompt is answerable only by the member whose turn raised it, in the
channel it was posted to — see
:class:`gateway.transports.buzz.pending_approvals.PendingApprovals`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from gateway.core.middleware.approvals import (
    MAX_APPROVAL_WAIT_SECONDS,
    ApprovalBroker,
    arguments_preview,
)
from gateway.transports.buzz.pending_approvals import PendingApprovals
from integrations.buzz import BuzzClient

logger = logging.getLogger("gateway")


class BuzzApprovalPrompter:
    """Posts a text approval prompt and waits for a reply resolving it."""

    def __init__(
        self,
        *,
        broker: ApprovalBroker,
        client: BuzzClient,
        channel_id: str,
        requester_pubkey: str,
        pending_approvals: PendingApprovals,
    ) -> None:
        self._broker = broker
        self._client = client
        self._channel_id = channel_id
        self._requester_pubkey = requester_pubkey
        self._pending_approvals = pending_approvals

    def request(
        self,
        *,
        tool_name: str,
        reason: str,
        arguments: Mapping[str, Any],
        expiry_seconds: float,
    ) -> tuple[bool, str]:
        approval_id = self._broker.create(platform="buzz", chat_id=self._channel_id)
        preview = arguments_preview(arguments)
        body = f"**Approval needed — `{tool_name}`**"
        if reason.strip():
            body += f"\n{reason.strip()}"
        if preview:
            body += f"\n```\n{preview}\n```"
        body += (
            f"\n\n`{self._requester_pubkey[:8]}…` — reply **approve** or **deny** "
            "to this message. Only you can answer it."
        )
        result = self._client.send_message(channel=self._channel_id, content=body)
        if not result["success"]:
            logger.warning(
                "[buzz-gateway] approval prompt post failed tool=%s channel=%s error=%s",
                tool_name,
                self._channel_id,
                result["error"],
            )
            return (False, "")

        event_id = result["event_id"]
        self._pending_approvals.register(
            event_id,
            approval_id=approval_id,
            requester_pubkey=self._requester_pubkey,
            channel_id=self._channel_id,
        )
        try:
            timeout = min(float(expiry_seconds), MAX_APPROVAL_WAIT_SECONDS)
            approved, decided_by = self._broker.wait(approval_id, timeout=timeout)
        finally:
            self._pending_approvals.discard(event_id)

        outcome = _outcome_text(tool_name, approved=approved, decided_by=decided_by)
        edit_result = self._client.edit_message(event_id=event_id, content=outcome)
        if not edit_result["success"]:
            logger.debug(
                "[buzz-gateway] approval outcome edit failed event=%s error=%s",
                event_id,
                edit_result["error"],
            )
        return (approved, decided_by)


def _outcome_text(tool_name: str, *, approved: bool, decided_by: str) -> str:
    if approved:
        return f"✅ `{tool_name}` approved by `{decided_by[:8]}…`"
    if decided_by:
        return f"🚫 `{tool_name}` denied by `{decided_by[:8]}…`"
    return f"⏱ Approval request for `{tool_name}` expired — action skipped."


__all__ = ["BuzzApprovalPrompter"]
