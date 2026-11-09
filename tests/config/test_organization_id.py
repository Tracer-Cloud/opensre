"""One answer to "which organization does this process serve".

Consumers spread across billing, analytics, Slack principal resolution and the
context-mount ownership check all read the same value through
:func:`organization_id`. The regression that motivates these tests: when a
deployment declared its organization under a name the product did not read, the
silo resolved an empty string and refused every Slack turn.
"""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.organization import organization_id


@pytest.fixture(autouse=True)
def _clear_org_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start unbound, whatever the developer's shell holds."""
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)


def test_resolves_the_deployments_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every deployment declares it under this one name."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_fargate")

    # Act / Assert
    assert organization_id() == "org_fargate"


def test_unbound_when_unset() -> None:
    """A laptop stays unbound rather than guessing an owner for customer data."""
    # Arrange: the autouse fixture cleared it.

    # Act / Assert
    assert organization_id() == ""


def test_whitespace_is_not_an_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank-but-present value must not bind a process to an empty owner."""
    # Arrange
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "   ")

    # Act / Assert
    assert organization_id() == ""


def test_a_silo_can_serve_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this single name exists to prevent.

    Principal resolution refused every Slack turn on a silo whose organization
    resolved empty, while the same control plane injected its Slack bot token.
    """
    # Arrange
    from gateway.transports.slack.processing.principal import resolve_slack_principal

    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_fargate")
    monkeypatch.delenv("OPENSRE_SILO_TEAM_IDS", raising=False)

    # Act
    principal = resolve_slack_principal(team_id="T_ACME")

    # Assert
    assert principal.id == "org_fargate"


def test_credits_and_usage_see_the_same_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metering and attribution read the same answer as everything else."""
    # Arrange
    from gateway.core.billing.credits_client import organization_id_for_silo
    from infrastructure.analytics.usage_context import get_organization_id

    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_fargate")

    # Act / Assert
    assert organization_id_for_silo() == "org_fargate"
    assert get_organization_id() == "org_fargate"
