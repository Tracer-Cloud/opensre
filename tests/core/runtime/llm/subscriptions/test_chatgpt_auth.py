"""Exercise OAuth security, session isolation, and rotating refresh persistence."""

from __future__ import annotations

import base64
import hashlib
import json
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives.asymmetric import rsa

from config.constants.chatgpt import CHATGPT_ISSUER, CHATGPT_RESOURCE
from config.llm_auth.credentials import status
from config.llm_auth.subscription_storage import (
    read_store,
    subscription_directory,
    subscription_lock,
    write_store,
)
from core.llm.subscriptions import chatgpt
from core.llm.subscriptions.types import SubscriptionLoginRequired
from surfaces.cli.app import cli


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_SUBSCRIPTION_AUTH_DIR", str(tmp_path / "subscriptions"))
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public["kid"] = "test-key"
    state: dict[str, Any] = {
        "attempts": [],
        "refreshes": [],
        "issued": "oaiapp_one",
        "subject": "user-one",
    }

    class Callback:
        redirect_uri = "http://127.0.0.1:1455/auth/callback"

        def __init__(self, pending_state: str) -> None:
            self.pending_state = pending_state

        def receive(self, authorize: Any, present: Any) -> dict[str, str]:
            url = authorize(self.redirect_uri)
            values = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
            state["attempts"].append(values)
            present(url)
            return {"state": self.pending_state, "code": "code-test", "client_id": state["issued"]}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "issuer": CHATGPT_ISSUER,
                    "jwks_uri": f"{CHATGPT_ISSUER}/jwks",
                    "revocation_endpoint": f"{CHATGPT_ISSUER}/revoke",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(HTTPStatus.OK, json={"keys": [public]})
        if request.url.path.endswith("/oauth/token"):
            form = {key: value[0] for key, value in parse_qs(request.content.decode()).items()}
            assert "client_secret" not in form
            assert form["resource"] == CHATGPT_RESOURCE
            if form["grant_type"] == "refresh_token":
                state["refreshes"].append(form)
                assert form["refresh_token"] == "refresh-original"
                assert form["client_id"] == "oaiapp_one"
                assert "scope" not in form
                if "refresh_error" in state:
                    return httpx.Response(
                        state.get("refresh_status", HTTPStatus.BAD_REQUEST),
                        json={"error": state["refresh_error"]},
                    )
                return httpx.Response(
                    HTTPStatus.OK,
                    json={
                        "access_token": "access-renewed",
                        "refresh_token": "refresh-rotated",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            attempt = state["attempts"][-1]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                .decode()
                .rstrip("=")
            )
            assert challenge == attempt["code_challenge"]
            assert form["redirect_uri"] == attempt["redirect_uri"]
            assert form["client_id"] == state["issued"]
            claims = {
                "iss": CHATGPT_ISSUER,
                "aud": state.get("aud", state["issued"]),
                "sub": state["subject"],
                "email": "same@example.test",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "nonce": state.get("nonce", attempt["nonce"]),
            }
            signer = (
                rsa.generate_private_key(public_exponent=65537, key_size=2048)
                if state.get("bad_signature")
                else private
            )
            token = jwt.encode(claims, signer, algorithm="RS256", headers={"kid": "test-key"})
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "access_token": "access-original",
                    "refresh_token": "refresh-original",
                    "id_token": token,
                    "token_type": "Bearer",
                    "scope": state.get(
                        "scope", "chatgpt.tokens.use.direct resource.invoke offline_access openid"
                    ),
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/v1/models":
            assert request.headers["Authorization"] in {
                "Bearer access-original",
                "Bearer access-renewed",
            }
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "models": [
                        {"slug": "hidden", "visibility": "hide"},
                        {"slug": "gpt-6.1-sol", "display_name": "Sol", "visibility": "list"},
                    ]
                },
            )
        if request.url.path == "/revoke":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [state["issued"]]
            state["revoked"] = form["token"][0]
            return httpx.Response(state.get("revoke_status", HTTPStatus.OK))
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(httpx.Client, "_transport_for_url", lambda _self, _url: transport)
    monkeypatch.setattr(chatgpt, "OAuthCallback", Callback)
    monkeypatch.setattr("webbrowser.open", lambda _url: True)
    return state


def _login() -> str:
    return chatgpt.login(lambda _url: None, select_model=lambda models: models[0][0])


def test_cli_login_keeps_api_keys_separate_and_status_never_reads_tokens(
    provider, monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENAI_API_KEY", "api-key-must-stay")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://wrong.example")
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "opensre-identity-must-stay")
    result = CliRunner().invoke(cli, ["auth", "login", "chatgpt", "--no-open-browser"])
    assert result.exit_code == 0, result.output
    env = (tmp_path / ".env").read_text()
    assert "LLM_PROVIDER=chatgpt\n" in env
    assert "OPENSRE_LLM_CONNECTION=local\n" in env
    assert "access-original" not in env and "refresh-original" not in env
    import os

    assert os.environ["OPENAI_API_KEY"] == "api-key-must-stay"
    assert os.environ["OPENSRE_ACCOUNT_TOKEN"] == "opensre-identity-must-stay"
    from config.account import account_llm_route

    assert account_llm_route() is None
    path = subscription_directory("chatgpt") / "auth.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert provider["attempts"][0]["client_id"] == "dynamic_agent_client"
    assert provider["attempts"][0]["agent_name_hint"] == "OpenSRE"
    path.unlink()
    # Rendering consults metadata only, even if credential storage disappears.
    assert status("chatgpt").configured

    def no_account_secret():
        raise AssertionError("status must not read the OpenSRE account token")

    monkeypatch.setattr("config.account.resolve_account_token", no_account_secret)
    result = CliRunner().invoke(cli, ["auth", "status", "chatgpt"])
    assert result.exit_code == 0
    assert "subscription" in result.output
    assert "access-original" not in result.output


@pytest.mark.parametrize(
    "field,value",
    [
        ("bad_signature", True),
        ("nonce", "wrong"),
        ("aud", "other-client"),
        ("scope", "openid resource.invoke"),
    ],
)
def test_invalid_identity_or_permission_does_not_replace_active_account(provider, field, value):
    _login()
    with subscription_lock("chatgpt"):
        original = read_store("chatgpt")["profiles"]["oaiapp_one"]
    provider[field] = value
    with pytest.raises(SubscriptionLoginRequired):
        _login()
    with subscription_lock("chatgpt"):
        assert read_store("chatgpt")["profiles"]["oaiapp_one"] == original
    assert "agent_name_hint" not in provider["attempts"][-1]


def test_profiles_with_same_email_remain_separate_and_logout_keeps_registration(provider):
    _login()
    host_id = provider["attempts"][-1]["ext_agent_host_id"]
    provider["issued"], provider["subject"] = "oaiapp_two", "user-two"
    chatgpt.login(lambda _url: None, select_model=lambda models: models[0][0], new_account=True)
    assert "Remote revocation" not in chatgpt.logout()
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
    assert len(store["profiles"]) == 2
    assert store["profiles"]["oaiapp_one"]["access_token"] == "access-original"
    assert "refresh_token" not in store["profiles"]["oaiapp_two"]
    assert store["profiles"]["oaiapp_two"]["subject"] == "user-two"
    assert store["host_id"] == host_id
    _login()
    assert provider["attempts"][-1]["client_id"] == "oaiapp_two"
    assert provider["attempts"][-1]["ext_agent_host_id"] == host_id
    provider["subject"] = "different-user"
    with pytest.raises(SubscriptionLoginRequired, match="identity changed"):
        _login()


def test_parallel_requests_refresh_once_and_persist_rotation(provider):
    _login()
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        store["profiles"]["oaiapp_one"]["expires_at"] = time.time() - 1
        write_store("chatgpt", store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: chatgpt.resolve(), range(2)))
    assert all(result.token == "access-renewed" for result in results)
    assert len(provider["refreshes"]) == 1
    with subscription_lock("chatgpt"):
        assert read_store("chatgpt")["profiles"]["oaiapp_one"]["refresh_token"] == "refresh-rotated"


@pytest.mark.parametrize(
    "code",
    [
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    ],
)
def test_terminal_refresh_error_clears_only_active_tokens_and_allows_relogin(provider, code):
    _login()
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
        active = store["profiles"]["oaiapp_one"]
        active["expires_at"] = time.time() - 1
        store["profiles"]["other"] = {"access_token": "other-access", "client_id": "other"}
        write_store("chatgpt", store)
    provider["refresh_error"] = code
    with pytest.raises(SubscriptionLoginRequired, match="login chatgpt"):
        chatgpt.resolve()
    with subscription_lock("chatgpt"):
        cleared = read_store("chatgpt")
    assert cleared["active"] == "oaiapp_one"
    assert cleared["host_id"] == store["host_id"]
    assert cleared["profiles"]["other"] == store["profiles"]["other"]
    assert cleared["profiles"]["oaiapp_one"]["client_id"] == "oaiapp_one"
    for key in ("access_token", "refresh_token", "id_token", "expires_at", "earliest_refresh_at"):
        assert key not in cleared["profiles"]["oaiapp_one"]
    assert not status("chatgpt").configured
    with pytest.raises(SubscriptionLoginRequired, match="sign in"):
        chatgpt.resolve()
    assert len(provider["refreshes"]) == 1
    del provider["refresh_error"]
    _login()
    assert provider["attempts"][-1]["client_id"] == "oaiapp_one"
    assert chatgpt.resolve().token == "access-original"


@pytest.mark.parametrize(
    "code,http_status",
    [
        ("invalid_client", HTTPStatus.BAD_REQUEST),
        ("temporarily_unavailable", HTTPStatus.SERVICE_UNAVAILABLE),
    ],
)
def test_nonterminal_refresh_errors_preserve_credentials(provider, code, http_status):
    _login()
    with subscription_lock("chatgpt"):
        original = read_store("chatgpt")
        original["profiles"]["oaiapp_one"]["expires_at"] = time.time() - 1
        write_store("chatgpt", original)
    provider["refresh_error"], provider["refresh_status"] = code, http_status
    with pytest.raises(SubscriptionLoginRequired):
        chatgpt.resolve()
    with subscription_lock("chatgpt"):
        assert read_store("chatgpt") == original
    assert status("chatgpt").configured


def test_failed_revocation_clears_local_tokens_and_reports_remote_failure(provider):
    _login()
    provider["revoke_status"] = HTTPStatus.SERVICE_UNAVAILABLE
    assert "Remote revocation was not confirmed" in chatgpt.logout()
    with pytest.raises(SubscriptionLoginRequired, match="sign in"):
        chatgpt.resolve()
    assert not status("chatgpt").configured


def test_cancel_before_model_selection_preserves_current_connection(provider, monkeypatch):
    import click

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    def cancel(_models):
        raise click.Abort()

    with pytest.raises(click.Abort):
        chatgpt.login(lambda _url: None, select_model=cancel)
    with subscription_lock("chatgpt"):
        store = read_store("chatgpt")
    assert "active" not in store
    assert store["profiles"] == {}
    assert store["pending"] == "oaiapp_one"


def test_disabled_credential_storage_does_not_write_files(provider, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    with pytest.raises(RuntimeError, match="local credential storage"):
        _login()
    assert not (tmp_path / "subscriptions").exists()


def test_login_without_provider_selection_saves_no_env_or_provider(provider, monkeypatch, tmp_path):
    import os

    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    result = CliRunner().invoke(
        cli, ["auth", "login", "chatgpt", "--no-open-browser", "--no-set-provider"]
    )
    assert result.exit_code == 0, result.output
    assert os.environ["LLM_PROVIDER"] == "deepseek"
    assert not (tmp_path / ".env").exists()
    assert chatgpt.resolve().token == "access-original"


def test_wizard_uses_browser_flow_and_account_models_without_prompting_for_key(
    provider, monkeypatch, tmp_path
):
    from surfaces.cli.wizard import flow
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    monkeypatch.setattr(flow, "local_defaults", lambda: {"provider": None, "model": ""})
    monkeypatch.setattr(
        flow, "_choose_onboarding_provider", lambda _default: PROVIDER_BY_VALUE["chatgpt"]
    )
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: 0)

    def forbid_static_or_api_prompt(*_args, **_kwargs):
        raise AssertionError("Subscriptions must use browser login and the account's model catalog")

    monkeypatch.setattr(flow, "choose_provider_model", forbid_static_or_api_prompt)
    monkeypatch.setattr(flow, "_prompt_validated_llm_credential", forbid_static_or_api_prompt)
    assert flow.run_llm_setup(show_header=False) == 0
    assert "LLM_PROVIDER=chatgpt" in (tmp_path / ".env").read_text()
    assert chatgpt.resolve().token == "access-original"
