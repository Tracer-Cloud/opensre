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


def test_a_warmed_session_also_drops_its_cache_after_a_store_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway sessions warm the cache before the first turn; that path stamps it too."""
    # Arrange: warmed at stamp 1, then the store is rewritten
    state = _store_versions(monkeypatch)
    session = SessionCore()
    session.integrations.warm()
    assert session.resolved_integrations_cache is not None
    state.update(stamp=2, token="token-two")

    # Act
    resolved = resolve_and_cache_integrations(session)

    # Assert: the warmed cache did not survive the rewrite
    assert resolved["github"]["auth_token"] == "token-two"
    assert state["resolutions"] == 2


@pytest.mark.parametrize("path", ["warm", "turn"])
def test_a_store_rewritten_during_resolution_is_re_resolved_next_turn(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Credentials read from the old store must not carry the new store's stamp."""
    # Arrange: the store is rewritten while the first resolution is in flight
    state = _store_versions(monkeypatch)
    session = SessionCore()
    real_resolve = integration_resolution.resolve_integrations

    def resolve_while_store_changes() -> dict[str, Any]:
        resolved = real_resolve()
        if state["resolutions"] == 1:
            state.update(stamp=2, token="token-two")
        return resolved

    monkeypatch.setattr(integration_resolution, "resolve_integrations", resolve_while_store_changes)

    # Act
    if path == "warm":
        session.integrations.warm()
    else:
        resolve_and_cache_integrations(session)
    after_rewrite = resolve_and_cache_integrations(session)

    # Assert: the in-flight result was stamped with the old store, so the next turn re-resolves
    assert after_rewrite["github"]["auth_token"] == "token-two"
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
