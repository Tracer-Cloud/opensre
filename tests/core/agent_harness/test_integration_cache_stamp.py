"""A session's cached credentials are dropped when the integrations store behind them is replaced."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness import SessionCore
from core.agent_harness.session import integration_resolution
from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations


def _store_versions(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The store as the resolver sees it: a stamp and the credentials it resolves to."""
    state: dict[str, Any] = {"stamp": 1, "token": "token-one", "resolutions": 0}

    def stamp() -> int:
        return int(state["stamp"])

    def resolve() -> dict[str, Any]:
        state["resolutions"] += 1
        return {"github": {"auth_token": state["token"]}}

    monkeypatch.setattr(integration_resolution, "integrations_store_stamp", stamp)
    monkeypatch.setattr(integration_resolution, "resolve_integrations", resolve)
    return state


def test_a_replaced_store_reaches_a_session_that_already_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the session resolved once; then the store is rewritten with a new token
    state = _store_versions(monkeypatch)
    session = SessionCore()
    first = resolve_and_cache_integrations(session)
    state.update(stamp=2, token="token-two")

    # Act
    second = resolve_and_cache_integrations(session)
    third = resolve_and_cache_integrations(session)

    # Assert: the old cache served once, the new token after the rewrite, then cached again
    assert first["github"]["auth_token"] == "token-one"
    assert second["github"]["auth_token"] == "token-two"
    assert third["github"]["auth_token"] == "token-two"
    assert state["resolutions"] == 2


def test_an_unchanged_store_keeps_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    state = _store_versions(monkeypatch)
    session = SessionCore()

    # Act
    for _ in range(3):
        resolve_and_cache_integrations(session)

    # Assert
    assert state["resolutions"] == 1
