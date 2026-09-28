"""Hosted credit reads stay distinct from auth failures and never invent zero."""

from __future__ import annotations

from http import HTTPStatus

import httpx
import pytest

from config import account_credits as ledger
from config.account import AccountRecord
from surfaces.shared import account_credits
from surfaces.shared.account_credits import AccountCredits, parse_credit_balance_payload
from surfaces.shared.account_session import AccountSessionState


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


def _balance_payload(*, total: int = 100_000) -> dict[str, object]:
    return {
        "monthly": 80_000,
        "monthly_limit": 100_000,
        "top_up": 20_000,
        "total": total,
        "resets_at": "2026-10-01T00:00:00.000Z",
        "plan_id": "team",
    }


def test_parse_accepts_a_real_zero_balance() -> None:
    parsed = parse_credit_balance_payload(_balance_payload(total=0))
    assert parsed == AccountCredits(
        total=0,
        monthly=80_000,
        monthly_limit=100_000,
        top_up=20_000,
        resets_at="2026-10-01T00:00:00.000Z",
        plan_id="team",
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"total": "100000"},
        {"total": True},
        {"total": -1},
        {"error": "credits_unavailable"},
        {"credits": None},
    ],
)
def test_parse_rejects_untrusted_payloads(payload: object) -> None:
    assert parse_credit_balance_payload(payload) is None


def test_parse_accepts_session_nested_credits_and_whole_floats() -> None:
    nested = parse_credit_balance_payload({"credits": {"total": 100_000.0}})
    assert nested is not None
    assert nested.total == 100_000


@pytest.fixture(autouse=True)
def _reset_hosted_credits_cache() -> None:
    ledger.reset_hosted_credits_cache()


def test_signed_out_never_calls_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "load_account_record", lambda: None)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "")

    def _should_not_fetch(*_args: object, **_kwargs: object) -> httpx.Response:
        raise AssertionError("credits must not be fetched while signed out")

    monkeypatch.setattr(ledger.httpx, "get", _should_not_fetch)

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.SIGNED_OUT
    assert status.credits is None


def test_fetch_sends_the_bearer_token_and_not_a_query_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(HTTPStatus.OK, json=_balance_payload())

    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(ledger.httpx, "get", fake_get)

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.ACTIVE
    assert status.credits is not None
    assert status.credits.total == 100_000
    assert captured["url"] == "https://app.opensre.com/api/credits/balance"
    assert "osre_pat_secret" not in str(captured["url"])
    assert captured["headers"] == {
        "Authorization": "Bearer osre_pat_secret",
        "Accept": "application/json",
    }


@pytest.mark.parametrize(
    ("status_code", "state"),
    [
        (HTTPStatus.UNAUTHORIZED, AccountSessionState.INVALID),
        (HTTPStatus.NOT_FOUND, AccountSessionState.UNAVAILABLE),
        (HTTPStatus.SERVICE_UNAVAILABLE, AccountSessionState.UNAVAILABLE),
    ],
)
def test_fetch_failures_are_not_a_zero_balance(
    monkeypatch: pytest.MonkeyPatch,
    status_code: HTTPStatus,
    state: AccountSessionState,
) -> None:
    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(
        ledger.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(status_code, json={"total": 0}),
    )

    status = account_credits.fetch_account_credits()

    assert status.state is state
    assert status.credits is None


@pytest.mark.parametrize(
    "balance_status",
    [HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN, HTTPStatus.FOUND, HTTPStatus.UNAUTHORIZED],
)
def test_fetch_falls_back_to_cli_session_credits_when_balance_route_rejects_the_pat(
    monkeypatch: pytest.MonkeyPatch,
    balance_status: HTTPStatus,
) -> None:
    captured: list[str] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        captured.append(url)
        headers = kwargs.get("headers")
        assert headers == {
            "Authorization": "Bearer osre_pat_secret",
            "Accept": "application/json",
        }
        if url.endswith("/api/credits/balance"):
            return httpx.Response(balance_status, json={"error": "not found"})
        return httpx.Response(
            HTTPStatus.OK,
            json={"credits": _balance_payload(), "user": {"id": "user_123"}},
        )

    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(ledger.httpx, "get", fake_get)

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.ACTIVE
    assert status.credits is not None
    assert status.credits.total == 100_000
    assert captured == [
        "https://app.opensre.com/api/credits/balance",
        "https://app.opensre.com/api/auth/cli/session",
    ]


def test_fetch_falls_back_to_cli_session_when_balance_returns_sign_in_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        if url.endswith("/api/credits/balance"):
            return httpx.Response(HTTPStatus.OK, text="<html>Sign in</html>")
        return httpx.Response(
            HTTPStatus.OK,
            json={"credits": _balance_payload(), "user": {"id": "user_123"}},
        )

    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(ledger.httpx, "get", fake_get)

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.ACTIVE
    assert status.credits is not None
    assert status.credits.total == 100_000


def test_verified_zero_balance_is_not_treated_as_unread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(
        ledger.httpx,
        "get",
        lambda *_args, **_kwargs: httpx.Response(HTTPStatus.OK, json=_balance_payload(total=0)),
    )

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.ACTIVE
    assert status.credits is not None
    assert status.credits.total == 0


def test_fetch_session_without_credits_is_not_a_zero_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **_kwargs: object) -> httpx.Response:
        if url.endswith("/api/credits/balance"):
            return httpx.Response(HTTPStatus.NOT_FOUND, json={"error": "not found"})
        return httpx.Response(HTTPStatus.OK, json={"user": {"id": "user_123"}, "credits": None})

    monkeypatch.setattr(ledger, "load_account_record", _record)
    monkeypatch.setattr(ledger, "resolve_account_token", lambda: "osre_pat_secret")
    monkeypatch.setattr(ledger.httpx, "get", fake_get)

    status = account_credits.fetch_account_credits()

    assert status.state is AccountSessionState.UNAVAILABLE
    assert status.credits is None
    assert "does not expose" not in status.detail
    assert "0" not in status.detail
