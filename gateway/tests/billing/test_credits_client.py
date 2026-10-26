from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from gateway.core.billing import turn_metering
from gateway.core.billing.credits_client import CreditsOutcome, consume_credits

_URL_ENV = "OPENSRE_WEBAPP_URL"
_SECRET_ENV = "AGENT_USAGE_SECRET"
_ORG_ENV = "ORGANIZATION_ID"


@pytest.fixture
def metering_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env with the webapp URL + shared secret set, so metering is live."""
    monkeypatch.setenv(_URL_ENV, "https://app.opensre.test")
    monkeypatch.setenv(_SECRET_ENV, "sekrit")


def test_unconfigured_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: URL + org present but no credential → hosted misconfiguration.
    monkeypatch.setenv(_URL_ENV, "https://app.opensre.test")
    monkeypatch.delenv(_SECRET_ENV, raising=False)

    def fail_if_called(*_a: object, **_k: object) -> httpx.Response:
        raise AssertionError("no network call when metering is unconfigured")

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", fail_if_called)

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: classified unconfigured without touching the network.
    assert outcome is CreditsOutcome.UNCONFIGURED


@pytest.mark.usefixtures("metering_on")
def test_unconfigured_when_org_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: secret set but no organization id anywhere (deploy misconfig).
    monkeypatch.delenv(_ORG_ENV, raising=False)

    # Act
    outcome = consume_credits(organization_id="", reason="slack_turn")

    # Assert: unconfigured, not denied — callers must not read this as "out of credits".
    assert outcome is CreditsOutcome.UNCONFIGURED


@pytest.mark.usefixtures("metering_on")
def test_402_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the ledger reports a shortfall.
    monkeypatch.setattr(
        "gateway.core.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(
            HTTPStatus.PAYMENT_REQUIRED, json={"balance": 0, "required": 1}
        ),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: the one genuine refuse-the-user state.
    assert outcome is CreditsOutcome.DENIED


@pytest.mark.usefixtures("metering_on")
def test_2xx_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the ledger consumes a credit and returns the remaining balance.
    monkeypatch.setattr(
        "gateway.core.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(HTTPStatus.OK, json={"balance": 41.5, "consumed": 1}),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert
    assert outcome is CreditsOutcome.ALLOWED


@pytest.mark.usefixtures("metering_on")
def test_transport_error_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: the webapp is unreachable — an our-side failure, not a user's shortfall.
    def unreachable(*_a: object, **_k: object) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", unreachable)

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: callers receive a non-admitting outcome and fail closed.
    assert outcome is CreditsOutcome.UNAVAILABLE


@pytest.mark.usefixtures("metering_on")
def test_transport_error_log_omits_exception_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Arrange: the exception message contains a URL that must never reach the log.
    sensitive_url = "https://app.opensre.internal/api/credits/consume"

    def unreachable(*_a: object, **_k: object) -> httpx.Response:
        raise httpx.ConnectError(f"[Errno -2] Name or service not known: {sensitive_url}")

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", unreachable)

    with caplog.at_level(logging.WARNING, logger="gateway.core.billing.credits_client"):
        consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: the log record was emitted but contains no part of the exception message.
    assert any("[credits] webapp unreachable" in r.message for r in caplog.records)
    for record in caplog.records:
        if "[credits] webapp unreachable" in record.message:
            assert sensitive_url not in record.message, (
                "Log must not expose the exception message (may contain the webapp URL)"
            )
            assert "Name or service not known" not in record.message, (
                "Log must not expose raw exception text"
            )
            # Only the exception type name is permitted.
            assert "ConnectError" in record.message


@pytest.mark.usefixtures("metering_on")
def test_5xx_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a server error from the ledger (any non-402, non-2xx status).
    monkeypatch.setattr(
        "gateway.core.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, json={}),
    )

    # Act
    outcome = consume_credits(organization_id="org_x", reason="slack_turn")

    # Assert: only 402 means exhausted; other error statuses cannot admit work.
    assert outcome is CreditsOutcome.UNAVAILABLE


@pytest.mark.usefixtures("metering_on")
def test_request_matches_webapp_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: capture the outbound request the client builds.
    calls: list[dict[str, Any]] = []

    def capture(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return httpx.Response(HTTPStatus.OK, json={"balance": 9, "consumed": 2})

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", capture)

    # Act
    outcome = consume_credits(
        "org_x", amount=2, reason="investigation", metadata={"investigationId": "inv-1"}
    )

    # Assert: bearer secret + the webapp's body shape ("amount", never "units").
    assert outcome is CreditsOutcome.ALLOWED
    (call,) = calls
    assert call["url"] == "https://app.opensre.test/api/credits/consume"
    assert call["headers"]["Authorization"] == "Bearer sekrit"
    assert call["timeout"] == 5.0
    assert call["json"] == {
        "amount": 2,
        "organizationId": "org_x",
        "reason": "investigation",
        "investigationId": "inv-1",
    }


@pytest.mark.usefixtures("metering_on")
def test_metadata_cannot_override_billing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: hostile metadata tries to zero the charge and swap the org/reason.
    calls: list[dict[str, Any]] = []

    def capture(_url: str, **kwargs: Any) -> httpx.Response:
        calls.append(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"balance": 1})

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", capture)

    # Act
    consume_credits(
        organization_id="org_real",
        amount=1,
        reason="slack_turn",
        metadata={"amount": 0, "organizationId": "org_injected", "reason": "free_ride"},
    )

    # Assert: the injected markers never reach the ledger; core fields keep their
    # real values (a zero-credit charge to another org must be impossible).
    (sent,) = calls
    assert sent["json"]["amount"] == 1.0
    assert sent["json"]["organizationId"] == "org_real"
    assert sent["json"]["reason"] == "slack_turn"


@pytest.mark.usefixtures("metering_on")
def test_a_replayed_delivery_repeats_one_org_scoped_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the polling transports replay a delivery after a restart, so the
    # same delivery id is consumed twice. Both attempts must carry the same key
    # for the ledger to collapse them into one debit.
    calls: list[dict[str, Any]] = []

    def capture(_url: str, **kwargs: Any) -> httpx.Response:
        calls.append(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"balance": 4})

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", capture)

    # Act: the crash-and-replay of one Telegram update.
    for _ in range(2):
        consume_credits(
            organization_id="org_real",
            reason="telegram_turn",
            idempotency_key="telegram:44271",
        )

    # Assert: byte-identical key, and scoped to the org so the same update id
    # under another tenant cannot be mistaken for this debit.
    first, second = (call["headers"]["Idempotency-Key"] for call in calls)
    assert first == second == "org_real:telegram:44271"


@pytest.mark.usefixtures("metering_on")
def test_no_idempotency_header_when_the_caller_names_no_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: an unkeyed caller must not send an empty or forged key, which the
    # webapp could otherwise treat as a repeat of a previous unkeyed charge.
    calls: list[dict[str, Any]] = []

    def capture(_url: str, **kwargs: Any) -> httpx.Response:
        calls.append(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"balance": 4})

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", capture)

    # Act
    consume_credits(organization_id="org_real", reason="smoke")

    # Assert
    (sent,) = calls
    assert "Idempotency-Key" not in sent["headers"]


@pytest.mark.parametrize("amount", [0, -1, 1.5, True])
def test_rejects_non_positive_or_fractional_amounts(amount: Any) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        consume_credits(organization_id="org_real", amount=amount, reason="slack_turn")


def test_creditsoutcome_is_strenum_with_identical_values() -> None:
    # Arrange: the persisted/logged string for each outcome must not move.
    expected_values = {
        CreditsOutcome.ALLOWED: "allowed",
        CreditsOutcome.DENIED: "denied",
        CreditsOutcome.DISABLED: "disabled",
        CreditsOutcome.UNCONFIGURED: "unconfigured",
        CreditsOutcome.UNAVAILABLE: "unavailable",
    }

    # Act / Assert: StrEnum members behave as their plain string value.
    for member, expected in expected_values.items():
        assert member.value == expected
        assert member == expected
        assert isinstance(member, str)


@pytest.mark.usefixtures("metering_on")
def test_a_metered_turn_names_its_member_on_the_charge(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a Slack member's turn on a gateway that meters per turn
    monkeypatch.setattr(turn_metering, "account_llm_route", lambda: None)
    calls: list[dict[str, Any]] = []

    def capture(_url: str, **kwargs: Any) -> httpx.Response:
        calls.append(kwargs)
        return httpx.Response(HTTPStatus.OK, json={"balance": 1})

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", capture)
    scope = StorageScope(principal=Principal.org("org_x"), actor=Actor(id="U0MEMBER"))

    # Act
    with (
        bound_storage_scope(scope),
        turn_metering.bound_turn_metering(
            organization_id="org_x", reason="slack_turn", idempotency_key="ts-1", on_denied=list
        ),
    ):
        admitted = turn_metering.admit_metered_turn()

    # Assert: Settings → Usage can chart the charge under the member who spent it
    assert admitted is True
    (sent,) = calls
    assert sent["json"]["actor"] == "U0MEMBER"
