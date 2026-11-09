"""Thread-safe registry of approval prompts awaiting a text reply.

Written by :class:`gateway.transports.buzz.approvals.BuzzApprovalPrompter` on
the turn's executor thread; read by the poll loop
(:mod:`gateway.transports.buzz.background`) on the asyncio loop thread — the
lock is what makes that safe, mirroring
:class:`gateway.core.middleware.approvals.ApprovalBroker`'s own locking.

Each entry carries the *request's* own authority, not just its approval id.
Buzz channels are multi-member, and a reply only has to quote the prompt's
event id to reach the poll loop — so matching on the prompt alone would let
any other participant approve or deny somebody else's protected write. The
requester and the channel the prompt was posted in are therefore part of the
match, on top of the live identity-policy check the poll loop still runs.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingApproval:
    """One posted approval prompt and the authority allowed to answer it."""

    approval_id: str
    requester_pubkey: str
    channel_id: str


class PendingApprovals:
    """Maps a posted approval-prompt event id -> its :class:`PendingApproval`."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def register(
        self,
        prompt_event_id: str,
        *,
        approval_id: str,
        requester_pubkey: str,
        channel_id: str,
    ) -> None:
        """Record a posted prompt so a reply to it can resolve the approval."""
        with self._lock:
            self._pending[prompt_event_id] = PendingApproval(
                approval_id=approval_id,
                requester_pubkey=requester_pubkey,
                channel_id=channel_id,
            )

    def discard(self, prompt_event_id: str) -> None:
        """Forget a prompt once its wait has returned (decided or expired)."""
        with self._lock:
            self._pending.pop(prompt_event_id, None)

    def find(self, reply_event_ids: frozenset[str]) -> PendingApproval | None:
        """Return the prompt one of *reply_event_ids* answers, without consuming it.

        Lets the caller tell "this reply is aimed at a live prompt" from "this
        is an ordinary mention" before deciding anything — and consume it only
        once the responder has cleared every check.
        """
        with self._lock:
            for target in reply_event_ids:
                pending = self._pending.get(target)
                if pending is not None:
                    return pending
        return None

    def claim(self, reply_event_ids: frozenset[str], *, pubkey: str, channel_id: str) -> str | None:
        """Consume and return the approval id *pubkey* may resolve, if any.

        The entry stays pending unless the responder is the member whose turn
        raised the request, replying in the channel the prompt was posted to.
        Leaving it pending is deliberate: a rejected attempt must not burn the
        slot the rightful responder still needs.
        """
        with self._lock:
            for target in reply_event_ids:
                pending = self._pending.get(target)
                if pending is None:
                    continue
                if pending.requester_pubkey != pubkey or pending.channel_id != channel_id:
                    return None
                del self._pending[target]
                return pending.approval_id
        return None

    def drain(self) -> list[str]:
        """Clear and return every outstanding approval id.

        Used at shutdown to release turns blocked in ``ApprovalBroker.wait``
        instead of leaving them to burn the full approval timeout.
        """
        with self._lock:
            approval_ids = [pending.approval_id for pending in self._pending.values()]
            self._pending.clear()
        return approval_ids


__all__ = ["PendingApproval", "PendingApprovals"]
