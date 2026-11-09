"""Dispatch tests for the OpenAI-compatible provider registry in ``llm_client``.

Guards the refactor that collapsed six near-identical ``elif provider == ...``
branches in ``_create_llm_client`` into ``_OPENAI_COMPATIBLE_PROVIDERS``. The
registry is pure data, so these assert both the data and that ``_create_llm_client``
wires each provider into the right transport with the right base URL, API-key env
var, temperature, feature-flag behavior, and model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm.factory import build_llm_client as _create_llm_client
from core.llm.providers.openai_compat_providers import OPENAI_COMPATIBLE_PROVIDERS
from core.llm.transports.litellm.clients import LiteLLMLLMClient
from core.llm.transports.sdk.llm_clients import OpenAILLMClient

_OPENAI_COMPATIBLE_PROVIDERS = OPENAI_COMPATIBLE_PROVIDERS


def test_registry_entries_are_well_formed() -> None:
    assert set(_OPENAI_COMPATIBLE_PROVIDERS) == {
        "openrouter",
        "trustedrouter",
        "deepseek",
        "gemini",
        "nvidia",
        "minimax",
        "groq",
        "ollama",
        "custom-openai",
    }
    for name, spec in _OPENAI_COMPATIBLE_PROVIDERS.items():
        if name in ("ollama", "custom-openai"):
            # Base URL is resolved from settings (ollama_host / custom_openai_base_url),
            # not a static constant.
            assert spec.base_url is None
        else:
            assert spec.base_url is not None and spec.base_url.startswith("http"), name
        assert spec.api_key_env.endswith("_API_KEY"), name
    # MiniMax is the only registry provider that pins a non-default temperature.
    assert _OPENAI_COMPATIBLE_PROVIDERS["minimax"].temperature == 1.0
    assert _OPENAI_COMPATIBLE_PROVIDERS["openrouter"].temperature is None


@pytest.mark.parametrize("provider", sorted(set(_OPENAI_COMPATIBLE_PROVIDERS) - {"custom-openai"}))
def test_create_llm_client_dispatches_registry_provider(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _OPENAI_COMPATIBLE_PROVIDERS[provider]
    settings = SimpleNamespace(
        provider=provider,
        ollama_model="stub-model",
        ollama_host="http://localhost:11434",
        **{f"{provider}_toolcall_model": "stub-model"},
    )
    monkeypatch.setattr("config.llm_settings.resolve_llm_settings", lambda: settings)
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: "test-key"
    )

    client = _create_llm_client("toolcall")

    assert isinstance(client, OpenAILLMClient)
    expected_base_url = "http://localhost:11434/v1" if provider == "ollama" else spec.base_url
    assert client._base_url == expected_base_url
    assert client._api_key_env == spec.api_key_env
    assert client._model == "stub-model"
    assert client._temperature == spec.temperature
    assert client._max_tokens == spec.config.max_tokens


@pytest.mark.parametrize("provider", sorted(set(_OPENAI_COMPATIBLE_PROVIDERS) - {"custom-openai"}))
def test_create_llm_client_dispatches_registry_provider_to_litellm_when_transport_enabled(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _OPENAI_COMPATIBLE_PROVIDERS[provider]
    settings = SimpleNamespace(
        provider=provider,
        ollama_model="stub-model",
        ollama_host="http://localhost:11434",
        **{f"{provider}_toolcall_model": "stub-model"},
    )
    monkeypatch.setattr("config.llm_settings.resolve_llm_settings", lambda: settings)
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: "test-key"
    )
    monkeypatch.setenv("OPENSRE_LLM_TRANSPORT", "litellm")

    client = _create_llm_client("toolcall")

    assert isinstance(client, LiteLLMLLMClient)
    expected_base_url = "http://localhost:11434/v1" if provider == "ollama" else spec.base_url
    assert client._api_base == expected_base_url
    assert client._api_key_env == spec.api_key_env
    # LiteLLM model is prefixed with "openai/" for compat providers
    assert client._litellm_model == "openai/stub-model"
    assert client._temperature == spec.temperature
    assert client._max_tokens == spec.config.max_tokens


def test_create_llm_client_uses_sdk_without_transport_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(provider="deepseek", deepseek_toolcall_model="deepseek-v4-flash")
    monkeypatch.setattr("config.llm_settings.resolve_llm_settings", lambda: settings)
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env_var: "test-key"
    )
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    client = _create_llm_client("toolcall")

    assert isinstance(client, OpenAILLMClient)
