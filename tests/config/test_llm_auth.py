from __future__ import annotations

from pathlib import Path

import pytest

import config.llm_auth
from config.account import AccountLLMRoute
from config.llm_auth.credentials import resolve_for_request
from config.llm_auth.records import resolve_provider_auth_record, save_provider_auth_record
from config.llm_credentials import resolve_env_credential
from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile
from surfaces.shared.llm_setup.auth_service import (
    AuthSetupError,
    configure_api_key_provider,
)
from surfaces.shared.llm_setup.validation import ValidationResult


def test_package_exports_are_all_defined() -> None:
    """Every ``__all__`` name must be a real attribute so ``import *`` cannot raise."""
    for name in config.llm_auth.__all__:
        assert hasattr(config.llm_auth, name), name


def test_resolve_auth_profile_accepts_api_key_providers() -> None:
    assert resolve_auth_profile("deepseek").provider_value == "deepseek"
    assert resolve_auth_profile("openai").provider_value == "openai"
    assert resolve_auth_profile("anthropic").provider_value == "anthropic"


def test_resolve_auth_profile_rejects_removed_subscription_aliases() -> None:
    with pytest.raises(KeyError):
        resolve_auth_profile("chatgpt")
    with pytest.raises(KeyError):
        resolve_auth_profile("claude")


def test_configure_deepseek_api_key_stores_secret_and_nonsecret_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "config.setup_store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.auth_service.validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="ok"),
    )

    env_path = tmp_path / ".env"
    result = configure_api_key_provider(
        profile=resolve_auth_profile("deepseek"),
        api_key="deepseek-secret",
        model="deepseek-v4-flash",
        env_path=env_path,
    )

    assert result.provider == "deepseek"
    assert resolve_env_credential("DEEPSEEK_API_KEY") == "deepseek-secret"
    env_content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=deepseek\n" in env_content
    assert "DEEPSEEK_REASONING_MODEL=deepseek-v4-flash\n" in env_content
    assert "DEEPSEEK_API_KEY=deepseek-secret\n" in env_content
    assert resolve_provider_auth_record("deepseek")["source"] == "fallback"
    assert result.source == "fallback"


def test_configure_api_key_reports_the_file_tier_it_actually_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`opensre auth login` must report the tier that actually stored the key."""
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "config.setup_store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.auth_service.validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="ok"),
    )

    result = configure_api_key_provider(
        profile=resolve_auth_profile("deepseek"),
        api_key="deepseek-secret",
        model="deepseek-v4-flash",
        env_path=tmp_path / ".env",
    )

    assert result.source == "fallback"
    assert "credentials.json" in result.detail
    assert resolve_provider_auth_record("deepseek")["source"] == "fallback"


def test_configure_api_key_does_not_store_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.auth_service.validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=False, detail="rejected"),
    )

    with pytest.raises(AuthSetupError, match="rejected"):
        configure_api_key_provider(
            profile=resolve_auth_profile("deepseek"),
            api_key="bad-key",
        )
    assert resolve_env_credential("DEEPSEEK_API_KEY") == ""


def test_account_login_locks_other_llm_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: AccountLLMRoute(
            base_url="https://app.opensre.com/api/llm/v1",
            model="gpt-5.4-mini",
        ),
    )

    with pytest.raises(AuthSetupError, match="managed by your OpenSRE account"):
        configure_api_key_provider(
            profile=resolve_auth_profile("deepseek"),
            api_key="must-not-be-saved",
            validate=False,
        )


def test_resolve_for_request_stales_when_credential_is_genuinely_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean miss is real evidence and should still stale."""
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="fallback",
        detail="DEEPSEEK_API_KEY stored in the local credentials file.",
        verified=True,
        stale=False,
        env_var="DEEPSEEK_API_KEY",
    )

    resolution = resolve_for_request("deepseek")

    assert resolution.ok is False
    record = resolve_provider_auth_record("deepseek")
    assert record["stale"] == "true"
    assert record["verified"] == "false"


def test_ignoring_the_account_route_survives_a_stored_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A rejected or unreachable login keeps its record and token on disk, so the
    # hosted route outlives the sign-in. Choosing a local model must win.
    from config.account import (
        AccountRecord,
        account_llm_route,
        ignore_account_llm_route,
        restore_account_llm_route,
    )

    record = AccountRecord(
        user_id="u1",
        email="dev@example.com",
        app_url="https://app.opensre.com",
        llm_provider="openai",
        llm_model="gpt-5.4-mini",
        organization_id="org1",
        signed_in_at="2026-09-08T00:00:00Z",
        token_expires_at="2026-12-08T00:00:00Z",
    )
    monkeypatch.setattr("config.account.load_account_record", lambda: record)
    monkeypatch.setattr("config.account.resolve_account_token", lambda: "token")
    # setenv (not delenv) so monkeypatch removes the flag this test sets below.
    monkeypatch.setenv("OPENSRE_IGNORE_ACCOUNT_ROUTE", "")

    assert account_llm_route() is not None

    ignore_account_llm_route()

    assert account_llm_route() is None

    # A later successful login replaces the rejected session, so the hosted
    # route has to come back; otherwise the shell stays local until restart.
    restore_account_llm_route()

    assert account_llm_route() is not None
