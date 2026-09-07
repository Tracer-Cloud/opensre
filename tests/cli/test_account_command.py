from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from click.testing import CliRunner
from rich.console import Console

from config.account import AccountRecord, load_account_record, save_account_record
from config.constants.github import GITHUB_CLI_REQUIRED_SCOPES
from integrations.github import PersonalGitHubSnapshot
from surfaces.cli import account_auth
from surfaces.cli.account_auth import AccountLoginResult
from surfaces.cli.account_ui import AccountLoginPresenter
from surfaces.cli.commands.account import account_command
from surfaces.shared.account_session import AccountSessionState, AccountStatus


class _RecordingProgress:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.urls: list[str] = []
        self.opened: list[bool] = []

    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        self.events.append("prompt")
        self.urls.append(url)
        self.opened.append(opened)

    def authorization_received(self) -> None:
        self.events.append("authorized")

    def setup_complete(self) -> None:
        self.events.append("complete")


def _capture_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _record() -> AccountRecord:
    return AccountRecord(
        user_id="user_123",
        organization_id="org_123",
        github_username="octocat",
        email="octocat@example.com",
        app_url="https://app.opensre.com",
        signed_in_at="2026-09-01T10:00:00+00:00",
        token_expires_at="2026-12-01T10:00:00+00:00",
        github_scopes=("read:org", "repo"),
    )


def test_account_record_is_owner_only_and_contains_no_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "account.json"
    monkeypatch.setenv("OPENSRE_ACCOUNT_METADATA_PATH", str(path))

    save_account_record(_record())

    assert load_account_record() == _record()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    content = path.read_text(encoding="utf-8")
    assert "access_token" not in content
    assert "osre_pat_" not in content


def test_login_uses_state_and_pkce_without_putting_tokens_in_browser_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_urls: list[str] = []
    exchanged: dict[str, str] = {}
    saved_tokens: list[str] = []
    saved_records: list[AccountRecord] = []
    cache_resets: list[bool] = []

    def fake_wait(*_args: object, **_kwargs: object) -> account_auth._CallbackResult:
        return account_auth._CallbackResult(code="osre_code_one_time")

    def fake_exchange(app_url: str, code: str, verifier: str) -> account_auth._ExchangeResult:
        exchanged.update(app_url=app_url, code=code, verifier=verifier)
        return account_auth._ExchangeResult(
            access_token="osre_pat_secret",
            token_expires_at="2026-12-01T10:00:00+00:00",
            user_id="user_123",
            organization_id="org_123",
            github_username="octocat",
            github_access_token="gho_secret",
            github_scopes=("repo",),
            llm_provider="openai",
            llm_model="gpt-5.4-mini",
            email="octocat@example.com",
        )

    monkeypatch.setattr(account_auth, "_wait_for_callback", fake_wait)
    monkeypatch.setattr(account_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "save_account_token", saved_tokens.append)
    monkeypatch.setattr(account_auth, "save_account_record", saved_records.append)
    monkeypatch.setattr(
        "core.llm.factory.reset_llm_clients",
        lambda: cache_resets.append(True),
    )
    monkeypatch.setattr(
        account_auth,
        "configure_personal_github",
        lambda **_kwargs: PersonalGitHubSnapshot(None),
    )

    def open_browser(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "claude-local")
    progress = _RecordingProgress()
    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        browser_open=open_browser,
        progress=progress,
    )

    assert result.record.github_username == "octocat"
    assert saved_tokens == ["osre_pat_secret"]
    assert saved_records == [result.record]
    assert cache_resets == [True]
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert os.environ["ANTHROPIC_REASONING_MODEL"] == "claude-local"
    assert len(opened_urls) == 1
    assert progress.events == ["prompt", "authorized", "complete"]
    assert progress.urls == opened_urls
    assert progress.opened == [True]
    assert "osre_pat_secret" not in opened_urls[0]
    assert "gho_secret" not in opened_urls[0]

    query = parse_qs(urlsplit(opened_urls[0]).query)
    verifier = exchanged["verifier"]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert query["code_challenge"] == [expected_challenge]
    assert len(query["state"][0]) >= 32


def test_exchange_rejects_github_login_without_integration_scopes() -> None:
    payload = {
        "access_token": "osre_pat_secret",
        "expires_at": "2026-12-01T10:00:00+00:00",
        "user": {"id": "user_123", "email": "octocat@example.com"},
        "organization": {"id": "org_123"},
        "github": {
            "username": "octocat",
            "access_token": "gho_secret",
            "scopes": ["read:user", "user:email"],
        },
        "llm": {"provider": "openai", "model": "gpt-5.4-mini"},
    }

    with pytest.raises(account_auth.AccountAuthError, match="missing required access"):
        account_auth._decode_exchange(payload)


def test_exchange_accepts_complete_github_integration_scopes() -> None:
    payload = {
        "access_token": "osre_pat_secret",
        "expires_at": "2026-12-01T10:00:00+00:00",
        "user": {"id": "user_123", "email": "octocat@example.com"},
        "organization": {"id": "org_123"},
        "github": {
            "username": "octocat",
            "access_token": "gho_secret",
            "scopes": sorted(GITHUB_CLI_REQUIRED_SCOPES),
        },
        "llm": {"provider": "openai", "model": "gpt-5.4-mini"},
    }

    exchange = account_auth._decode_exchange(payload)

    assert set(exchange.github_scopes) == GITHUB_CLI_REQUIRED_SCOPES


def test_login_warns_when_env_token_would_override_and_does_not_revoke_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[tuple[str, str]] = []

    def fake_wait(*_args: object, **_kwargs: object) -> account_auth._CallbackResult:
        return account_auth._CallbackResult(code="osre_code_one_time")

    def fake_exchange(*_args: object, **_kwargs: object) -> account_auth._ExchangeResult:
        return account_auth._ExchangeResult(
            access_token="osre_pat_new",
            token_expires_at="2026-12-01T10:00:00+00:00",
            user_id="user_123",
            organization_id="org_123",
            github_username="octocat",
            github_access_token="gho_secret",
            github_scopes=("repo",),
            llm_provider="openai",
            llm_model="gpt-5.4-mini",
            email="octocat@example.com",
        )

    def fake_revoke(app_url: str, token: str) -> bool:
        revoked.append((app_url, token))
        return True

    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_from_env")
    monkeypatch.setattr(account_auth, "_wait_for_callback", fake_wait)
    monkeypatch.setattr(account_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(account_auth, "_revoke_remote", fake_revoke)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "osre_pat_file_old")
    monkeypatch.setattr(account_auth, "save_account_token", lambda _token: None)
    monkeypatch.setattr(account_auth, "save_account_record", lambda _record: None)
    monkeypatch.setattr(
        account_auth,
        "configure_personal_github",
        lambda **_kwargs: PersonalGitHubSnapshot(None),
    )

    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        open_browser=False,
    )

    assert "OPENSRE_ACCOUNT_TOKEN" in result.warning
    assert revoked == [("https://app.opensre.com", "osre_pat_file_old")]


def test_logout_without_personal_account_preserves_manual_github_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disconnected = False

    def disconnect() -> bool:
        nonlocal disconnected
        disconnected = True
        return True

    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "delete_account_token", lambda: None)
    monkeypatch.setattr(account_auth, "delete_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "disconnect_personal_github", disconnect)

    result = account_auth.logout_account()

    assert result.remote_revoked is True
    assert disconnected is False


def test_logout_revokes_the_file_token_not_an_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[tuple[str, str]] = []

    def fake_revoke(app_url: str, token: str) -> bool:
        revoked.append((app_url, token))
        return True

    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "osre_pat_from_env")
    monkeypatch.setattr(account_auth, "load_account_record", _record)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "osre_pat_file")
    monkeypatch.setattr(account_auth, "delete_account_token", lambda: None)
    monkeypatch.setattr(account_auth, "delete_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "disconnect_personal_github", lambda: True)
    monkeypatch.setattr(account_auth, "_revoke_remote", fake_revoke)

    result = account_auth.logout_account()

    assert revoked == [("https://app.opensre.com", "osre_pat_file")]
    assert "OPENSRE_ACCOUNT_TOKEN" in result.detail


def test_login_presenter_prints_url_and_numbered_steps() -> None:
    url = (
        "http://localhost:3000/cli/auth/github"
        "?callback_port=43721&state=login-state&code_challenge=pkce"
    )
    console, buf = _capture_console()
    presenter = AccountLoginPresenter(console)

    presenter.prompt_sign_in(url, opened=True)
    presenter.authorization_received()
    presenter.setup_complete()

    output = buf.getvalue()
    assert "Sign in to OpenSRE with GitHub:" in output
    assert "1. Your browser will open this link" in output
    assert url in output
    assert "2. Sign in with GitHub." in output
    assert "3. Connect repository and security access." in output
    assert "Waiting for you to approve in the browser" in output
    assert "Browser authorization received." in output
    assert "GitHub integration connected." in output
    assert "Hosted model activated." in output


def test_login_presenter_success_shows_hosted_model_and_store() -> None:
    console, buf = _capture_console()
    presenter = AccountLoginPresenter(console)

    presenter.success(AccountLoginResult(record=_record(), warning=""))

    output = buf.getvalue()
    assert "Signed in as @octocat" in output
    assert "octocat@example.com" in output
    assert "openai · gpt-5.4-mini" in output
    assert "hosted by OpenSRE" in output
    assert "store" in output


def test_login_presenter_warns_when_a_session_is_already_active() -> None:
    console, buf = _capture_console()
    presenter = AccountLoginPresenter(console)
    status = AccountStatus(AccountSessionState.ACTIVE, _record(), "Authenticated with GitHub.")

    presenter.warn_active_session(status)
    presenter.session_kept()

    output = buf.getvalue()
    assert "A session is already active" in output
    assert "@octocat" in output
    assert "This session is valid" in output
    assert "Keeping the current session" in output
    assert "opensre account login --force" in output


def _invoke_account_login(*args: str, json_output: bool = False):
    return CliRunner().invoke(
        account_command,
        ["login", *args],
        obj={"json": json_output},
    )


def test_login_keeps_valid_session_without_starting_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "surfaces.cli.commands.account.account_status",
        lambda **_: AccountStatus(AccountSessionState.ACTIVE, _record(), "ok"),
    )

    def _should_not_login(**_kwargs: object) -> AccountLoginResult:
        raise AssertionError("login must not start while a session is active")

    monkeypatch.setattr("surfaces.cli.account_auth.login_account", _should_not_login)

    result = _invoke_account_login("--no-browser")

    assert result.exit_code == 0, result.output
    assert "already active" in result.output
    assert "@octocat" in result.output
    assert "Keeping the current session" in result.output


def test_login_json_reports_already_active_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.commands.account.account_status",
        lambda **_: AccountStatus(AccountSessionState.ACTIVE, _record(), "ok"),
    )

    def _should_not_login(**_kwargs: object) -> AccountLoginResult:
        raise AssertionError("login must not start while a session is active")

    monkeypatch.setattr("surfaces.cli.account_auth.login_account", _should_not_login)

    result = _invoke_account_login("--no-browser", json_output=True)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["already_active"] is True
    assert payload["authenticated"] is True
    assert payload["account"]["github_username"] == "octocat"


def test_login_force_replaces_valid_session(monkeypatch: pytest.MonkeyPatch) -> None:
    login_calls: list[object] = []
    monkeypatch.setattr(
        "surfaces.cli.commands.account.account_status",
        lambda **_: AccountStatus(AccountSessionState.ACTIVE, _record(), "ok"),
    )

    def fake_login(**_kwargs: object) -> AccountLoginResult:
        login_calls.append(True)
        return AccountLoginResult(record=_record())

    monkeypatch.setattr("surfaces.cli.account_auth.login_account", fake_login)

    result = _invoke_account_login("--no-browser", "--force")

    assert result.exit_code == 0, result.output
    assert login_calls == [True]
    assert "Replacing the active session for @octocat" in result.output
    assert "Signed in as @octocat" in result.output
