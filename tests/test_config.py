from __future__ import annotations

import sys
import types

import pytest
from pydantic import ValidationError

from app.config import LLMSettings


@pytest.fixture(autouse=True)
def _clear_enigm_agent_url(monkeypatch) -> None:
    monkeypatch.delenv("ENIGM_AGENT_URL", raising=False)


def test_llm_settings_reject_provider_typos_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="Did you mean 'openai'"):
        LLMSettings.model_validate(
            {
                "provider": "opneai",
                "openai_api_key": "sk-test",
            }
        )


def test_llm_settings_require_api_key_for_selected_provider() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        LLMSettings.model_validate({"provider": "openai"})


def test_llm_settings_from_env_uses_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "stored-secret" if env_var == "OPENAI_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"
    assert settings.openai_api_key == "stored-secret"


def test_llm_settings_from_env_uses_enigmagent_override(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeVaultClient:
        def __init__(self, host: str, port: int, timeout: float, origin: str) -> None:
            seen["init"] = {
                "host": host,
                "port": port,
                "timeout": timeout,
                "origin": origin,
            }

        def resolve(self, placeholder: str, origin: str | None = None) -> str:
            seen["resolve"] = {
                "placeholder": placeholder,
                "origin": origin,
            }
            return "vault-openai-key"

    monkeypatch.setitem(
        sys.modules, "enigmagent", types.SimpleNamespace(VaultClient=FakeVaultClient)
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ENIGM_AGENT_URL", "http://127.0.0.1:3737")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "stored-secret" if env_var == "OPENAI_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.openai_api_key == "vault-openai-key"
    assert seen["init"] == {
        "host": "127.0.0.1",
        "port": 3737,
        "timeout": 5.0,
        "origin": "http://localhost",
    }
    assert seen["resolve"] == {
        "placeholder": "OPENAI_API_KEY",
        "origin": "http://localhost",
    }


def test_llm_settings_from_env_enigmagent_ignored_errors_fallback(monkeypatch) -> None:
    class MissingVaultSecretError(Exception):
        code = "not_found"

    class FakeVaultClient:
        def __init__(self, host: str, port: int, timeout: float, origin: str) -> None:
            pass

        def resolve(self, placeholder: str, origin: str | None = None) -> str:
            assert placeholder == "OPENAI_API_KEY"
            assert origin == "http://localhost"
            raise MissingVaultSecretError("secret is not available in EnigmAgent")

    monkeypatch.setitem(
        sys.modules, "enigmagent", types.SimpleNamespace(VaultClient=FakeVaultClient)
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ENIGM_AGENT_URL", "http://127.0.0.1:3737")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "stored-secret" if env_var == "OPENAI_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"
    assert settings.openai_api_key == "stored-secret"


def test_llm_settings_from_env_rejects_non_local_plain_http_enigmagent_url(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ENIGM_AGENT_URL", "http://vault.example.internal:3737")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "stored-secret" if env_var == "OPENAI_API_KEY" else "",
    )

    with pytest.raises(
        ValueError,
        match="ENIGM_AGENT_URL only supports plain HTTP for localhost/127.0.0.1/::1",
    ):
        LLMSettings.from_env()


def test_llm_settings_require_minimax_api_key() -> None:
    with pytest.raises(ValidationError, match="MINIMAX_API_KEY"):
        LLMSettings.model_validate({"provider": "minimax"})


def test_llm_settings_minimax_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "minimax",
            "minimax_api_key": "mm-test-key",
        }
    )
    assert settings.provider == "minimax"
    assert settings.minimax_api_key == "mm-test-key"
    assert settings.minimax_reasoning_model == "MiniMax-M2.7"
    assert settings.minimax_toolcall_model == "MiniMax-M2.7-highspeed"


def test_llm_settings_from_env_minimax(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.config.resolve_llm_api_key",
        lambda env_var: "mm-stored-key" if env_var == "MINIMAX_API_KEY" else "",
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "minimax"
    assert settings.minimax_api_key == "mm-stored-key"


def test_llm_settings_from_env_max_tokens_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    settings = LLMSettings.from_env()

    assert settings.max_tokens == 8192


def test_llm_settings_from_env_max_tokens_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    with pytest.raises((ValueError, ValidationError)):
        LLMSettings.from_env()


def test_llm_settings_from_env_max_tokens_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)
    monkeypatch.setattr("app.config.resolve_llm_api_key", lambda _: "")

    from app.config import DEFAULT_MAX_TOKENS

    settings = LLMSettings.from_env()

    assert settings.max_tokens == DEFAULT_MAX_TOKENS
