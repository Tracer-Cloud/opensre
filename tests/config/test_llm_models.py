from __future__ import annotations

from config.llm_auth.provider_catalog import PROVIDER_SPECS
from config.llm_models import PROVIDER_MODEL_DEFAULTS, model_config_for
from config.llm_settings import LLMSettings


def test_provider_model_defaults_cover_tiered_provider_specs() -> None:
    for spec in PROVIDER_SPECS:
        if not spec.toolcall_model_env:
            continue
        assert spec.value in PROVIDER_MODEL_DEFAULTS, (
            f"ProviderSpec '{spec.value}' has tiered model envs but no PROVIDER_MODEL_DEFAULTS row"
        )
        defaults = PROVIDER_MODEL_DEFAULTS[spec.value]
        assert defaults.provider == spec.value
        assert defaults.settings_key
        if defaults.requires_explicit_models:
            assert not defaults.reasoning
            assert not defaults.classification
            assert not defaults.toolcall
        else:
            assert defaults.reasoning
            assert defaults.classification
            assert defaults.toolcall


def test_provider_model_defaults_settings_keys_match_llm_settings() -> None:
    fields = LLMSettings.model_fields
    for defaults in PROVIDER_MODEL_DEFAULTS.values():
        if defaults.single_model_settings:
            assert f"{defaults.settings_key}_model" in fields
            continue
        for tier in ("reasoning", "classification", "toolcall"):
            attr = f"{defaults.settings_key}_{tier}_model"
            assert attr in fields, f"missing LLMSettings field {attr}"


def test_model_config_for_matches_catalog_row() -> None:
    config = model_config_for("deepseek")
    defaults = PROVIDER_MODEL_DEFAULTS["deepseek"]
    assert config.reasoning_model == defaults.reasoning
    assert config.classification_model == defaults.classification
    assert config.toolcall_model == defaults.toolcall


def test_llm_settings_from_env_honors_primary_model_override(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_REASONING_MODEL", "deepseek-custom-reasoning")
    monkeypatch.setenv("DEEPSEEK_CLASSIFICATION_MODEL", "deepseek-custom-class")
    monkeypatch.setenv("DEEPSEEK_TOOLCALL_MODEL", "deepseek-custom-tool")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.deepseek_reasoning_model == "deepseek-custom-reasoning"
    assert settings.deepseek_classification_model == "deepseek-custom-class"
    assert settings.deepseek_toolcall_model == "deepseek-custom-tool"


def test_llm_settings_from_env_honors_legacy_model_fallback(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_CLASSIFICATION_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_TOOLCALL_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/legacy-shared")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    settings = LLMSettings.from_env()

    assert settings.openrouter_reasoning_model == "openrouter/legacy-shared"
    assert settings.openrouter_classification_model == "openrouter/legacy-shared"
    assert settings.openrouter_toolcall_model == "openrouter/legacy-shared"
