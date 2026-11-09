from __future__ import annotations

from unittest.mock import MagicMock

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.transports.buzz.approvals import BuzzApprovalPrompter
from gateway.transports.buzz.pending_approvals import PendingApprovals
from integrations.buzz.client import BuzzClient

REQUESTER = "a" * 64


def _client() -> MagicMock:
    return MagicMock(spec=BuzzClient)


def test_request_posts_prompt_registers_and_resolves_on_approve() -> None:
    client = _client()
    client.send_message.return_value = {"success": True, "error": "", "event_id": "prompt1"}
    client.edit_message.return_value = {"success": True, "error": ""}
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = BuzzApprovalPrompter(
        broker=broker,
        client=client,
        channel_id="chan-1",
        requester_pubkey=REQUESTER,
        pending_approvals=pending,
    )

    # Simulate the poll loop resolving the approval before request() waits.
    assert pending.find(frozenset()) is None  # sanity check empty state

    def _resolve_soon() -> None:
        # request() registers under client.send_message's return; grab it via the broker.
        matched = pending.claim(frozenset({"prompt1"}), pubkey=REQUESTER, channel_id="chan-1")
        assert matched is not None
        broker.resolve(matched, approved=True, decided_by=REQUESTER)

    # Patch broker.wait to trigger resolution synchronously instead of a real thread race.
    original_wait = broker.wait

    def _wait_and_resolve(approval_id: str, *, timeout: float) -> tuple[bool, str]:
        _resolve_soon()
        return original_wait(approval_id, timeout=timeout)

    broker.wait = _wait_and_resolve  # type: ignore[method-assign]

    approved, decided_by = prompter.request(
        tool_name="buzz_send_message",
        reason="posting a summary",
        arguments={"api_key": "sk-should-not-appear", "channel": "opensre-test"},
        expiry_seconds=30,
    )

    assert approved is True
    assert decided_by == REQUESTER
    posted = client.send_message.call_args.kwargs["content"]
    assert "sk-should-not-appear" not in posted
    assert "opensre-test" in posted
    client.edit_message.assert_called_once()
    assert "approved" in client.edit_message.call_args.kwargs["content"]
    # Cleaned up after resolution.
    assert pending.find(frozenset({"prompt1"})) is None


def test_request_expiry_edits_outcome_and_denies() -> None:
    client = _client()
    client.send_message.return_value = {"success": True, "error": "", "event_id": "prompt1"}
    client.edit_message.return_value = {"success": True, "error": ""}
    broker = ApprovalBroker()
    pending = PendingApprovals()
    prompter = BuzzApprovalPrompter(
        broker=broker,
        client=client,
        channel_id="chan-1",
        requester_pubkey=REQUESTER,
        pending_approvals=pending,
    )

    approved, decided_by = prompter.request(
        tool_name="buzz_send_message", reason="", arguments={}, expiry_seconds=0.01
    )

    assert approved is False
    assert decided_by == ""
    assert "expired" in client.edit_message.call_args.kwargs["content"]
    assert pending.find(frozenset({"prompt1"})) is None
