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

_ENV_TOKEN = "osre_personal_token"


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


class _Clock:
    """A monotonic clock the test advances explicitly."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value


class _Remote:
    """Stands in for the webapp session request and records each token validated."""

    def __init__(self) -> None:
        self.state = RemoteSessionState.VALID
        self.calls: list[tuple[str, str]] = []

    def validate(self, app_url: str, token: str, **_kwargs: object) -> RemoteSessionState:
        self.calls.append((app_url, token))
        return self.state


@pytest.fixture(autouse=True)
def _isolated_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_METADATA_PATH_ENV, str(tmp_path / "account.json"))
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, _ENV_TOKEN)
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)
    account_module._clear_account_route_session_cache()


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> _Remote:
    fake = _Remote()
    monkeypatch.setattr("config.account_session.validate_remote_session", fake.validate)
    return fake


def test_a_valid_login_keeps_the_hosted_route(remote: _Remote) -> None:
    save_account_record(_record())

    route = account_llm_route()

    assert route is not None
    assert route.base_url == "https://app.example/api/llm/v1"


@pytest.mark.parametrize(
    "state",
    [RemoteSessionState.INVALID, RemoteSessionState.UNAVAILABLE],
)
def test_a_login_that_no_longer_validates_falls_back(
    remote: _Remote, state: RemoteSessionState
) -> None:
    save_account_record(_record())
    remote.state = state

    assert account_llm_route() is None


def test_an_expired_record_with_its_own_token_falls_back_without_a_request(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record(expires_hours=-1))
    # The effective token is the stored one the expired record describes.
    monkeypatch.setattr(account_module, "stored_account_token", lambda: _ENV_TOKEN)

    assert account_llm_route() is None
    assert remote.calls == []


def test_an_expired_record_does_not_reject_a_fresh_env_token(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record(expires_hours=-1))
    monkeypatch.setattr(account_module, "stored_account_token", lambda: "osre_expired_stored_token")

    assert account_llm_route() is not None
    assert len(remote.calls) == 1


def test_an_invalid_stored_app_url_falls_back_without_a_request(remote: _Remote) -> None:
    save_account_record(_record(app_url="https://app.example?next=1"))

    assert account_llm_route() is None
    assert remote.calls == []


def test_the_verdict_is_cached_between_calls(remote: _Remote) -> None:
    save_account_record(_record())

    account_llm_route()
    account_llm_route()

    assert len(remote.calls) == 1


def test_a_cached_verdict_expires_after_the_ttl(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record())
    clock = _Clock()
    monkeypatch.setattr(account_module, "time", clock)

    assert account_llm_route() is not None  # caches a valid verdict

    clock.value = 120.0
    remote.state = RemoteSessionState.INVALID

    assert account_llm_route() is None
    assert len(remote.calls) == 2


def test_a_cached_failure_recovers_after_the_ttl(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record())
    clock = _Clock()
    monkeypatch.setattr(account_module, "time", clock)
    remote.state = RemoteSessionState.UNAVAILABLE

    assert account_llm_route() is None

    clock.value = 120.0
    remote.state = RemoteSessionState.VALID

    assert account_llm_route() is not None
    assert len(remote.calls) == 2


def test_changing_the_token_does_not_reuse_the_verdict(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record())

    account_llm_route()
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "osre_other_token")
    account_llm_route()

    assert [token for _, token in remote.calls] == [_ENV_TOKEN, "osre_other_token"]


def test_a_gateway_token_without_a_record_is_not_validated(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://gateway.example")
    remote.state = RemoteSessionState.INVALID

    route = account_llm_route()

    assert route is not None
    assert route.base_url == "https://gateway.example/api/llm/v1"
    assert remote.calls == []


def test_a_stale_login_uses_the_configured_provider(
    monkeypatch: pytest.MonkeyPatch, remote: _Remote
) -> None:
    save_account_record(_record())
    remote.state = RemoteSessionState.INVALID
    monkeypatch.setattr("config.llm_settings.resolve_llm_settings", _ollama_settings)
    monkeypatch.setattr(
        "infrastructure.harness_providers.cli_provider_registration",
        lambda _provider: None,
    )
    monkeypatch.setattr("core.llm.factory.use_litellm_for_provider", lambda _provider: True)

    from core.llm.factory import resolve_llm_route

    assert resolve_llm_route().provider == "ollama"
