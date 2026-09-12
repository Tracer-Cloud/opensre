"""Hosted account routing falls back when the stored session is stale."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, local

import pytest

from config import account
from config.account import AccountLLMRoute, AccountRecord
from config.account_validation import AccountValidation, AccountValidationState


def _record() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        email=None,
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at="2026-12-01T10:00:00+00:00",
        llm_model="gpt-5.4-mini",
    )


def test_stale_hosted_route_falls_back_until_validation_cache_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validations = iter(
        [
            AccountValidation(
                AccountValidationState.UNAVAILABLE,
                "The OpenSRE app could not be reached to validate this login.",
            ),
            AccountValidation(
                AccountValidationState.ACTIVE,
                "Authenticated with OpenSRE; LLM provider: openai (gpt-5.5).",
                llm_provider="openai",
                llm_model="gpt-5.5",
                expires_at="2026-12-01T10:00:00+00:00",
            ),
        ]
    )
    calls = 0

    def _validate(**_kwargs: object) -> AccountValidation:
        nonlocal calls
        calls += 1
        return next(validations)

    times: Iterator[float] = iter((100.0, 100.0, 130.0, 161.0, 161.0))
    monkeypatch.setattr(account, "load_account_record", _record)
    monkeypatch.setattr(account, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(account, "validate_account_session", _validate)
    monkeypatch.setattr(account, "monotonic", lambda: next(times))
    monkeypatch.setattr(account, "_account_route_cache", None)

    assert account.account_llm_route() is None
    assert account.account_llm_route() is None
    assert account.account_llm_route() == AccountLLMRoute(
        base_url="https://app.opensre.com/api/llm/v1",
        model="gpt-5.5",
    )
    assert calls == 2


def test_concurrent_cache_misses_share_one_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = 4
    outer_lookup_barrier = Barrier(workers)
    thread_state = local()
    calls = 0
    calls_lock = Lock()
    original_cache_lookup = account._cached_account_route

    def _synchronized_cache_lookup(
        key: tuple[AccountRecord, bytes], now: float
    ) -> account._AccountRouteCacheEntry | None:
        if not getattr(thread_state, "outer_lookup_complete", False):
            thread_state.outer_lookup_complete = True
            cached = original_cache_lookup(key, now)
            assert cached is None
            outer_lookup_barrier.wait(timeout=2)
            return cached
        return original_cache_lookup(key, now)

    def _validate(**_kwargs: object) -> AccountValidation:
        nonlocal calls
        with calls_lock:
            calls += 1
        return AccountValidation(AccountValidationState.UNAVAILABLE, "offline")

    monkeypatch.setattr(account, "load_account_record", _record)
    monkeypatch.setattr(account, "resolve_account_token", lambda: "token")
    monkeypatch.setattr(account, "validate_account_session", _validate)
    monkeypatch.setattr(account, "monotonic", lambda: 100.0)
    monkeypatch.setattr(account, "_cached_account_route", _synchronized_cache_lookup)
    monkeypatch.setattr(account, "_account_route_cache", None)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _index: account.account_llm_route(), range(workers)))

    assert results == [None] * workers
    assert calls == 1


def test_account_route_cache_does_not_retain_raw_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(account, "load_account_record", _record)
    monkeypatch.setattr(account, "resolve_account_token", lambda: "sensitive-token")
    monkeypatch.setattr(
        account,
        "validate_account_session",
        lambda **_kwargs: AccountValidation(
            AccountValidationState.UNAVAILABLE,
            "offline",
        ),
    )
    monkeypatch.setattr(account, "monotonic", lambda: 100.0)
    monkeypatch.setattr(account, "_account_route_cache", None)

    assert account.account_llm_route() is None
    cached = account._account_route_cache
    assert cached is not None
    assert b"sensitive-token" not in cached.key[1]
