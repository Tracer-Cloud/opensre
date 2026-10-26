from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.llm_auth.credentials import CredentialStatus
from config.llm_settings import (
    LLMSettings,
    describe_llm_resolution,
    has_credentials_for_active_llm_provider,
    llm_provider_error_context,
    resolve_llm_settings,
    resolve_llm_settings_verbose,
)


def test_llm_settings_reject_provider_typos_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="Did you mean 'openai'"):
        LLMSettings.model_validate(
            {
                "provider": "opneai",
                "openai_api_key": "sk-test",
            }
        )


def test_llm_settings_accepts_missing_api_key_for_selected_provider() -> None:
    settings = LLMSettings.model_validate({"provider": "openai"})

    assert settings.provider == "openai"
    assert settings.openai_api_key == ""


def test_llm_settings_from_env_does_not_read_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential",
        lambda env_var: (_ for _ in ()).throw(AssertionError(f"unexpected lookup: {env_var}")),
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"
    assert settings.openai_api_key == ""


def test_llm_settings_from_env_does_not_probe_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential",
        lambda env_var: (_ for _ in ()).throw(AssertionError(f"unexpected lookup: {env_var}")),
    )

    settings = LLMSettings.from_env()

    assert settings.provider == "openai"
    assert settings.openai_api_key == ""


def test_llm_settings_from_env_keyless_provider_does_not_resolve_keyring(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "codex")

    settings = LLMSettings.from_env()

    assert settings.provider == "codex"


def test_llm_settings_accepts_minimax_without_api_key() -> None:
    settings = LLMSettings.model_validate({"provider": "minimax"})

    assert settings.provider == "minimax"
    assert settings.minimax_api_key == ""


def test_llm_settings_accepts_deepseek_without_api_key() -> None:
    settings = LLMSettings.model_validate({"provider": "deepseek"})

    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == ""


def test_llm_settings_deepseek_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "deepseek",
            "deepseek_api_key": "ds-test-key",
        }
    )
    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == "ds-test-key"
    assert settings.deepseek_reasoning_model == "deepseek-v4-pro"
    assert settings.deepseek_classification_model == "deepseek-v4-flash"
    assert settings.deepseek_toolcall_model == "deepseek-v4-flash"


def test_llm_settings_from_env_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "deepseek"
    assert settings.deepseek_api_key == ""


def test_llm_settings_accepts_vertex_ai_without_project() -> None:
    settings = LLMSettings.model_validate({"provider": "vertex-ai"})

    assert settings.provider == "vertex-ai"
    assert settings.vertex_ai_project == ""
    assert settings.vertex_ai_location == "us-central1"


def test_llm_settings_from_env_vertex_ai(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "vertex-ai")
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.delenv("VERTEX_AI_LOCATION", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "vertex-ai"
    assert settings.vertex_ai_project == "my-gcp-project"
    assert settings.vertex_ai_location == "us-central1"
    assert settings.vertex_ai_reasoning_model == "gemini-2.5-pro"
    assert settings.vertex_ai_toolcall_model == "gemini-2.5-flash-lite"


def test_llm_settings_from_env_vertex_ai_custom_location(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "vertex-ai")
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "europe-west1")

    settings = LLMSettings.from_env()

    assert settings.vertex_ai_location == "europe-west1"


def test_llm_settings_minimax_provider_accepted() -> None:
    settings = LLMSettings.model_validate(
        {
            "provider": "minimax",
            "minimax_api_key": "mm-test-key",
        }
    )
    assert settings.provider == "minimax"
    assert settings.minimax_api_key == "mm-test-key"
    assert settings.minimax_reasoning_model == "MiniMax-M3"
    assert settings.minimax_toolcall_model == "MiniMax-M2.7-highspeed"


def test_llm_settings_from_env_minimax(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "minimax"
    assert settings.minimax_api_key == ""


@pytest.mark.parametrize(
    "raw_host, expected",
    [
        ("localhost:11434", "http://localhost:11434"),
        ("192.168.1.5:11434", "http://192.168.1.5:11434"),
        ("my-server:11434", "http://my-server:11434"),
        ("http://localhost:11434", "http://localhost:11434"),
        ("https://ollama.internal", "https://ollama.internal"),
    ],
)
def test_llm_settings_ollama_host_protocol_normalized(raw_host: str, expected: str) -> None:
    settings = LLMSettings.model_validate({"provider": "ollama", "ollama_host": raw_host})
    assert settings.ollama_host == expected


def test_llm_settings_from_env_max_tokens_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")

    settings = LLMSettings.from_env()

    assert settings.max_tokens == 8192


def test_llm_settings_from_env_max_tokens_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")

    with pytest.raises((ValueError, ValidationError)):
        LLMSettings.from_env()


def test_llm_settings_from_env_max_tokens_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("LLM_MAX_TOKENS", raising=False)

    from config.llm_models import DEFAULT_MAX_TOKENS

    settings = LLMSettings.from_env()

    assert settings.max_tokens == DEFAULT_MAX_TOKENS


def test_llm_settings_from_env_claude_code_without_api_key(monkeypatch) -> None:
    """CLI-backed Claude Code: onboard writes LLM_PROVIDER only; no hosted API key."""
    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "claude-code"


def test_llm_settings_from_env_gemini_cli_without_api_key(monkeypatch) -> None:
    """CLI-backed Gemini CLI provider should not require GEMINI_API_KEY in config validation."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini-cli")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.provider == "gemini-cli"


def test_llm_settings_from_env_copilot_without_api_key(monkeypatch) -> None:
    """CLI-backed Copilot CLI: vendor auth, no hosted API key required."""
    monkeypatch.setenv("LLM_PROVIDER", "copilot")

    settings = LLMSettings.from_env()

    assert settings.provider == "copilot"


def test_llm_settings_copilot_provider_accepted() -> None:
    settings = LLMSettings.model_validate({"provider": "copilot"})
    assert settings.provider == "copilot"


def test_has_credentials_for_active_llm_provider_missing_key(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert has_credentials_for_active_llm_provider() is False


def test_resolve_llm_settings_does_not_fall_back_to_openai_when_default_anthropic_key_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    settings = resolve_llm_settings()

    assert settings.provider == "anthropic"
    assert settings.anthropic_api_key == ""
    assert has_credentials_for_active_llm_provider() is False


def test_resolve_llm_settings_does_not_probe_other_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    settings = resolve_llm_settings()

    assert settings.provider == "openrouter"
    assert settings.openrouter_api_key == ""


def test_has_credentials_for_active_llm_provider_with_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_ollama_never_requires_key(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_cli_uses_status(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "copilot")
    monkeypatch.setattr(
        "config.llm_settings.credential_status",
        lambda provider: CredentialStatus(
            provider=provider,
            configured=True,
            source="cli",
            verified=True,
            stale=False,
            detail="CLI authenticated.",
        ),
    )

    assert has_credentials_for_active_llm_provider() is True


def test_has_credentials_for_active_llm_provider_re_raises_non_key_validation_errors(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MAX_TOKENS", "0")

    with pytest.raises(ValidationError, match="greater than 0"):
        has_credentials_for_active_llm_provider()


def test_has_credentials_for_active_llm_provider_re_raises_invalid_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")

    with pytest.raises(ValidationError, match="Unsupported LLM provider"):
        has_credentials_for_active_llm_provider()


def test_resolve_llm_settings_verbose_reports_no_fallback_when_configured_key_present(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    resolution = resolve_llm_settings_verbose()

    assert resolution.resolved_provider == "openai"
    assert resolution.configured_provider == "openai"
    assert resolution.fell_back is False
    assert resolution.missing_key_env is None


def test_resolve_llm_settings_verbose_reports_no_fallback_when_other_key_present(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")

    resolution = resolve_llm_settings_verbose()

    assert resolution.configured_provider == "openai"
    assert resolution.resolved_provider == "openai"
    assert resolution.fell_back is False
    assert resolution.missing_key_env is None
    assert "openai" in resolution.summary()


def test_resolve_llm_settings_verbose_does_not_warn_without_fallback(monkeypatch, caplog) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    with caplog.at_level("WARNING", logger="config.llm_settings"):
        resolve_llm_settings_verbose()

    assert not [r for r in caplog.records if "falling back" in r.getMessage()]


def test_resolve_llm_settings_verbose_attempts_only_configured_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    resolution = resolve_llm_settings_verbose(fallback_providers=("anthropic", "deepseek"))

    assert resolution.attempted_providers == ("openai",)
    assert resolution.fell_back is False


def test_describe_llm_resolution_reports_no_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))

    report = describe_llm_resolution()

    assert "configured provider : openai" in report
    assert "resolved provider   : openai" in report
    assert "fell back           : no" in report
    assert "credential status   : none" in report


def test_describe_llm_resolution_reports_missing_configured_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))

    report = describe_llm_resolution()

    assert "resolved provider   : openai" in report
    assert "credential status   : none" in report
    assert "OPENAI_API_KEY" in report


def test_llm_provider_error_context_uses_configured_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    context = llm_provider_error_context()

    assert context == "[LLM provider: openai]"


def test_llm_provider_error_context_no_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    assert llm_provider_error_context() == "[LLM provider: openai]"


def test_llm_provider_error_context_never_raises(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")

    assert llm_provider_error_context() == ""
