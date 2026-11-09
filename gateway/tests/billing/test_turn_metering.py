"""The shared gateway credit admission consumes only the bound turn."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome
from gateway.core.billing.turn_metering import admit_metered_turn, bound_turn_metering


@pytest.mark.parametrize(
    "outcome",
    [CreditsOutcome.ALLOWED, CreditsOutcome.DISABLED],
)
def test_allowed_or_deliberately_disabled_metering_admits_the_bound_turn(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CreditsOutcome,
) -> None:
    consume = MagicMock(return_value=outcome)
    denied = MagicMock()
    monkeypatch.setattr(turn_metering, "consume_credits", consume)

    with bound_turn_metering(
        organization_id="org_metered",
        reason="telegram_turn",
        idempotency_key="telegram:44271",
        on_denied=denied,
    ):
        admitted = admit_metered_turn()

    assert admitted is True
    consume.assert_called_once_with(
        "org_metered", reason="telegram_turn", idempotency_key="telegram:44271", actor=None
    )
    denied.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [CreditsOutcome.UNCONFIGURED, CreditsOutcome.UNAVAILABLE],
)
def test_untrustworthy_metering_outcomes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    outcome: CreditsOutcome,
) -> None:
    denied = MagicMock()
    monkeypatch.setattr(
        turn_metering,
        "consume_credits",
        MagicMock(return_value=outcome),
    )

    with (
        bound_turn_metering(
            organization_id="org_metered",
            reason="slack_turn",
            idempotency_key="slack:1712.0001",
            on_denied=denied,
        ),
        pytest.raises(
            turn_metering.CreditMeteringUnavailableError,
            match="refusing unmetered work",
        ),
    ):
        admit_metered_turn()

    denied.assert_not_called()


def test_denied_outcome_owns_the_response_and_rejects_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = MagicMock()
    monkeypatch.setattr(
        turn_metering,
        "consume_credits",
        MagicMock(return_value=CreditsOutcome.DENIED),
    )

    with bound_turn_metering(
        organization_id="org_metered",
        reason="buzz_turn",
        idempotency_key="buzz:ev_1",
        on_denied=denied,
    ):
        admitted = admit_metered_turn()

    assert admitted is False
    denied.assert_called_once_with()


def test_unbound_turn_is_a_programming_error() -> None:
    with pytest.raises(RuntimeError, match="no bound metering request"):
        admit_metered_turn()
