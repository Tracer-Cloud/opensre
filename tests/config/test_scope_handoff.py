"""A CLI child honours a scope hand-off only for the organization its deployment serves."""

from __future__ import annotations

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.tenancy import TURN_ACTOR_ID_ENV, TURN_ORGANIZATION_ID_ENV
from config.scope_handoff import handed_off_scope


def test_a_handoff_naming_another_organization_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a silo serving org_A, and an environment claiming a member of org_B
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_A")
    monkeypatch.setenv(TURN_ORGANIZATION_ID_ENV, "org_B")
    monkeypatch.setenv(TURN_ACTOR_ID_ENV, "U_EVE")

    # Act / Assert: nothing is attributed to an organization this process does not serve
    assert handed_off_scope() is None
