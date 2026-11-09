"""Tests for Clerk M2M token mint + in-process cache."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import gateway.core.billing.clerk_tokens as clerk_tokens
from config.constants.billing import MACHINE_SECRET_ENV


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clerk_tokens.clear_token_cache()
    yield
    clerk_tokens.clear_token_cache()


def test_mint_empty_without_machine_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MACHINE_SECRET_ENV, raising=False)
    assert clerk_tokens.webapp_access_token() == ""


def test_mint_posts_to_clerk_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test_secret")
    calls: list[dict[str, Any]] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"token": "mt_abc", "expires_in": 3600}

        @property
        def text(self) -> str:
            return ""

    def fake_post(url: str, **kwargs: Any) -> _Resp:
        calls.append({"url": url, **kwargs})
        return _Resp()

    monkeypatch.setattr(clerk_tokens.httpx, "post", fake_post)

    assert clerk_tokens.webapp_access_token() == "mt_abc"
    assert clerk_tokens.webapp_access_token() == "mt_abc"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.clerk.com/v1/m2m_tokens"
    assert calls[0]["headers"]["Authorization"] == "Bearer ak_test_secret"
    assert calls[0]["json"]["seconds_until_expiration"] == 3600


def test_mint_force_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test_secret")
    n = {"i": 0}

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            n["i"] += 1
            return {"token": f"mt_{n['i']}", "expires_in": 3600}

        @property
        def text(self) -> str:
            return ""

    monkeypatch.setattr(clerk_tokens.httpx, "post", lambda *_a, **_k: _Resp())
    assert clerk_tokens.webapp_access_token() == "mt_1"
    assert clerk_tokens.webapp_access_token(force_refresh=True) == "mt_2"


def test_mint_http_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MACHINE_SECRET_ENV, "ak_test_secret")
    monkeypatch.setattr(
        clerk_tokens.httpx,
        "post",
        lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )
    assert clerk_tokens.webapp_access_token() == ""
