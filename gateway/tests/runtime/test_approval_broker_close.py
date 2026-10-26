"""A closed approval broker must never park a turn on a decision that cannot arrive."""

from __future__ import annotations

import time

from gateway.core.middleware.approvals import MAX_APPROVAL_WAIT_SECONDS, ApprovalBroker


def test_approval_requested_after_close_fails_closed_without_waiting() -> None:
    """The turn a shutdown denial releases can ask again; that ask must not block.

    ``close()`` unblocks the waiters it can see, but the released turn runs on
    to its next tool call. Without the closed guard that second approval waits
    the full expiry and holds the executor thread shutdown is trying to join.
    """
    # Arrange
    broker = ApprovalBroker()
    broker.close()

    # Act
    approval_id = broker.create(platform="telegram", chat_id="chat-1")
    started = time.monotonic()
    approved, decided_by = broker.wait(approval_id, timeout=MAX_APPROVAL_WAIT_SECONDS)
    elapsed = time.monotonic() - started

    # Assert
    assert (approved, decided_by) == (False, "")
    assert elapsed < 1.0, "closed broker still blocked on a decision that cannot arrive"
