"""A stored login that stopped validating must not keep the hosted LLM route."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config import account as account_module
from config.account import AccountRecord, account_llm_route, save_account_record
from config.account_session import RemoteSessionState
from config.constants.account import OPENSRE_ACCOUNT_METADATA_PATH_ENV, OPENSRE_ACCOUNT_TOKEN_ENV
from config.constants.billing import WEBAPP_URL_ENV


def _record(*, expires_hours: int = 24, app_url: str = "https://app.example") -> AccountRecord:
    expires_at = datetime.now(UTC) + timedelta(hours=expires_hours)
    return AccountRecord(
        user_id="user_1",
        organization_id="org_1",
        email=None,
        app_url=app_url,
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at=expires_at.isoformat(),
    )


def _ollama_settings(**_kwargs: object) -> object:
    return type("_Settings", (), {"provider": "ollama"})()


@pytest.fixture(autouse=True)
def _isolated_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_METADATA_PATH_ENV, str(tmp_path / "account.json"))
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "osre_personal_token")
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    account_module._clear_account_route_session_cache()


def _verdict(monkeypatch: pytest.MonkeyPatch, state: RemoteSessionState) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def _validate(app_url: str, token: str) -> RemoteSessionState:
        calls.append((app_url, token))
        return state

    monkeypatch.setattr("config.account_session.validate_remote_session", _validate)
    return calls


def test_a_valid_login_keeps_the_hosted_route(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record())
    _verdict(monkeypatch, RemoteSessionState.VALID)

    route = account_llm_route()

    assert route is not None
    assert route.base_url == "https://app.example/api/llm/v1"


def test_a_revoked_login_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record())
    _verdict(monkeypatch, RemoteSessionState.INVALID)

    assert account_llm_route() is None


def test_an_unreachable_login_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record())
    _verdict(monkeypatch, RemoteSessionState.UNAVAILABLE)

    assert account_llm_route() is None


def test_an_expired_login_falls_back_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record(expires_hours=-1))
    calls = _verdict(monkeypatch, RemoteSessionState.VALID)

    assert account_llm_route() is None
    assert calls == []


def test_an_invalid_stored_app_url_falls_back_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_account_record(_record(app_url="https://app.example?next=1"))
    calls = _verdict(monkeypatch, RemoteSessionState.VALID)

    assert account_llm_route() is None
    assert calls == []


def test_the_verdict_is_cached_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record())
    calls = _verdict(monkeypatch, RemoteSessionState.VALID)

    account_llm_route()
    account_llm_route()

    assert len(calls) == 1


def test_a_gateway_token_without_a_record_is_not_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://gateway.example")
    calls = _verdict(monkeypatch, RemoteSessionState.INVALID)

    route = account_llm_route()

    assert route is not None
    assert route.base_url == "https://gateway.example/api/llm/v1"
    assert calls == []


def test_a_stale_login_uses_the_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    save_account_record(_record())
    _verdict(monkeypatch, RemoteSessionState.INVALID)
    monkeypatch.setattr("config.llm_settings.resolve_llm_settings", _ollama_settings)
    monkeypatch.setattr(
        "infrastructure.harness_providers.cli_provider_registration",
        lambda _provider: None,
    )
    monkeypatch.setattr("core.llm.factory.use_litellm_for_provider", lambda _provider: True)

    from core.llm.factory import resolve_llm_route

    assert resolve_llm_route().provider == "ollama"
