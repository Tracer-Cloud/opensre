from __future__ import annotations

from pathlib import Path

import pytest

import config.llm_auth
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


def test_resolve_auth_profile_accepts_underscore_alias() -> None:
    # Users copy env-style names (`custom_openai`) from older guidance.
    assert resolve_auth_profile("custom_openai").provider_value == "custom-openai"
    assert resolve_auth_profile("Custom_OpenAI").provider_value == "custom-openai"


def test_chatgpt_has_its_own_subscription_profile() -> None:
    assert resolve_auth_profile("chatgpt").kind == "subscription"
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


def test_successful_explicit_provider_login_selects_local_billing_without_logging_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from config.account import account_llm_route
    from config.constants.account import OPENSRE_ACCOUNT_TOKEN_ENV
    from config.constants.billing import WEBAPP_URL_ENV

    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENSRE_LLM_CONNECTION", raising=False)
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "existing-opensre-account")
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example")
    assert account_llm_route() is not None
    result = configure_api_key_provider(
        profile=resolve_auth_profile("deepseek"),
        api_key="test-key",
        validate=False,
        env_path=tmp_path / ".env",
    )
    assert result.provider == "deepseek"
    assert account_llm_route() is None
    import os

    assert os.environ[OPENSRE_ACCOUNT_TOKEN_ENV] == "existing-opensre-account"


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
