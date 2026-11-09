"""Write-tool approvals require an explicit allowlist even under open-guild."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.core.middleware.approvals import APPROVE_ACTION_ID
from gateway.transports.discord.approvals import handle_component_interaction


def _interaction(*, user_id: str, approval_id: str = "appr-1") -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        data={"custom_id": f"{APPROVE_ACTION_ID}:{approval_id}"},
    )


def test_approval_denied_when_allowlist_empty() -> None:
    broker = MagicMock()
    ok = handle_component_interaction(
        _interaction(user_id="111"),  # type: ignore[arg-type]
        broker=broker,
        allowed_user_ids=[],
    )
    assert ok is False
    broker.resolve.assert_not_called()


def test_approval_denied_for_unlisted_user() -> None:
    broker = MagicMock()
    ok = handle_component_interaction(
        _interaction(user_id="666"),  # type: ignore[arg-type]
        broker=broker,
        allowed_user_ids=["111"],
    )
    assert ok is False
    broker.resolve.assert_not_called()


def test_approval_allowed_for_listed_user() -> None:
    broker = MagicMock()
    broker.resolve.return_value = True
    ok = handle_component_interaction(
        _interaction(user_id="111"),  # type: ignore[arg-type]
        broker=broker,
        allowed_user_ids=["111"],
    )
    assert ok is True
    broker.resolve.assert_called_once_with("appr-1", approved=True, decided_by="111")
