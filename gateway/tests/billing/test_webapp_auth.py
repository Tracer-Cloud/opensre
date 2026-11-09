"""Tests for silo → webapp credential selection (M2M preferred, shared fallback)."""

from __future__ import annotations

import pytest

import gateway.core.billing.webapp_auth as webapp_auth
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV


def test_prefers_minted_m2m_token_over_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: both credentials available.
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared-secret-marker")
    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", lambda: "mt_from_clerk")

    # Act
    token = webapp_auth.webapp_bearer_token()

    # Assert: the org-scoped token wins; the shared secret does not leak through.
    assert token == "mt_from_clerk"
    assert "shared-secret-marker" not in token


def test_falls_back_to_shared_secret_when_mint_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: machine secret set but minting yields nothing.
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", lambda: "")

    # Act / Assert
    assert webapp_auth.webapp_bearer_token() == "shared"


def test_falls_back_to_shared_secret_when_mint_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: minting blows up (Clerk unreachable, bad config, anything).
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")

    def _boom() -> str:
        raise RuntimeError("clerk exploded")

    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", _boom)

    # Act / Assert: a mint failure must degrade, never propagate into the turn.
    assert webapp_auth.webapp_bearer_token() == "shared"


def test_uses_shared_secret_when_no_machine_secret_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: shared secret only. Minting must not be attempted.
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")

    def _should_not_mint() -> str:
        raise AssertionError("must not mint without a machine secret")

    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", _should_not_mint)

    # Act / Assert
    assert webapp_auth.webapp_bearer_token() == "shared"


def test_machine_token_never_falls_back_to_shared_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routes requiring a machine token reject the shared secret, so the
    machine-only accessor must return "" rather than substituting it."""
    # Arrange: shared secret present, no machine secret.
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.setenv(USAGE_SECRET_ENV, "SHARED-SECRET-LEAK-MARKER")

    # Act
    token = webapp_auth.webapp_machine_token()

    # Assert: empty, and the shared secret did not leak through.
    assert token == ""
    assert "SHARED-SECRET-LEAK-MARKER" not in token


def test_machine_token_empty_when_mint_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: machine secret set, but minting yields nothing / raises.
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_x")
    monkeypatch.setenv(USAGE_SECRET_ENV, "shared")
    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", lambda: "")
    assert webapp_auth.webapp_machine_token() == ""

    def _boom() -> str:
        raise RuntimeError("clerk exploded")

    monkeypatch.setattr(webapp_auth.clerk_tokens, "webapp_access_token", _boom)
    assert webapp_auth.webapp_machine_token() == ""


def test_empty_when_nothing_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: neither credential set — callers receive no usable bearer token.
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    monkeypatch.delenv(USAGE_SECRET_ENV, raising=False)

    # Act / Assert
    assert webapp_auth.webapp_bearer_token() == ""
