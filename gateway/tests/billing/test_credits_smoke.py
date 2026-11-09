"""Smoke tests for per-org credit metering: safe default posture + happy path.

These exercise the whole path through real env resolution (not surgical patches of
internals), covering the two operational states a deploy actually sees: metering
off (local/dev, nothing configured) and a fully configured silo whose ledger
accepts the charge.
"""

from __future__ import annotations

from http import HTTPStatus

import httpx
import pytest

from config.constants.billing import ORGANIZATION_ID_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from gateway.core.billing.credits_client import (
    CreditsOutcome,
    consume_credits,
    organization_id_for_silo,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No metering env set — the default local/dev posture."""
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV, ORGANIZATION_ID_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_metering_off_by_default_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: with nothing configured, the credit gate must never call out.
    def explode(*_a: object, **_k: object) -> httpx.Response:
        raise AssertionError("metering is off — no HTTP call may be made")

    monkeypatch.setattr("gateway.core.billing.credits_client.httpx.post", explode)
    monkeypatch.setattr("gateway.core.billing.credits_client.agent_bearer_token", explode)

    # Act
    outcome = consume_credits(reason="smoke")

    # Assert: local/self-hosted mode is explicitly disabled without a webapp URL.
    assert outcome is CreditsOutcome.DISABLED


@pytest.mark.usefixtures("clean_env")
def test_organization_id_for_silo_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_smoke")

    # Act
    org = organization_id_for_silo()

    # Assert
    assert org == "org_smoke"


def test_configured_happy_path_charges_and_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a fully configured silo + a ledger that accepts the charge.
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.opensre.test")
    monkeypatch.setenv(USAGE_SECRET_ENV, "sekrit")
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_smoke")
    monkeypatch.setattr(
        "gateway.core.billing.credits_client.httpx.post",
        lambda *_a, **_k: httpx.Response(HTTPStatus.OK, json={"balance": 10, "consumed": 1}),
    )

    # Act
    outcome = consume_credits(reason="smoke")

    # Assert: end-to-end through env resolution → request build → classification.
    assert outcome is CreditsOutcome.ALLOWED
