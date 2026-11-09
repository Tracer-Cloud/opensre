"""Custom OpenAI-/Anthropic-compatible gateway providers (issue #2682).

Covers config validation + normalization, routing through both transports, the
Anthropic-SDK base-URL threading, the SDK-only guard, and the reviewer asks
(normalize-once URL joining, redacted diagnostics, and fallback paths staying on
the custom base URL instead of jumping back to the default endpoint).
"""

from __future__ import annotations

import pytest

from config.constants.llm import normalize_custom_base_url
from config.llm_settings import LLMSettings
from core.llm.client_builders import build_agent_client, build_reasoning_client
from core.llm.providers.custom_endpoints import redact_base_url
from core.llm.transports.litellm.routing import (
    build_litellm_agent_client,
    build_litellm_llm_client,
)
from core.llm.transports.sdk.agent_clients import AnthropicAgentClient, OpenAIAgentClient
from core.llm.transports.sdk.llm_clients import LLMClient, OpenAILLMClient
from core.llm.types import LLMRoute


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub credential resolution so client construction never touches the keyring."""
    monkeypatch.setattr(
        "config.llm_credentials.resolve_env_credential", lambda _env: "test-key", raising=False
    )
    monkeypatch.setattr(
        "core.llm.providers.provider_credentials.resolve_llm_api_key", lambda _env: "test-key"
    )


def _openai_settings(**overrides: object) -> LLMSettings:
    payload = {
        "provider": "custom-openai",
        "custom_openai_base_url": "http://localhost:4000/v1",
        "custom_openai_reasoning_model": "gpt-5.4",
        "custom_openai_classification_model": "gpt-5.4",
        "custom_openai_toolcall_model": "gpt-5.4-mini",
    }
    payload.update(overrides)
    return LLMSettings.model_validate(payload)


def _anthropic_settings(**overrides: object) -> LLMSettings:
    payload = {
        "provider": "custom-anthropic",
        "custom_anthropic_base_url": "https://proxy.example.com",
        "custom_anthropic_reasoning_model": "claude-opus-4-7",
        "custom_anthropic_classification_model": "claude-opus-4-7",
        "custom_anthropic_toolcall_model": "claude-haiku-4-5",
    }
    payload.update(overrides)
    return LLMSettings.model_validate(payload)


def _route(settings: LLMSettings, *, use_litellm: bool = False) -> LLMRoute:
    return LLMRoute(
        settings=settings,
        provider=settings.provider,
        cli_provider_registration=None,
        use_litellm=use_litellm,
    )


# --------------------------------------------------------------------------- #
# Config validation + normalization
# --------------------------------------------------------------------------- #


def test_base_url_normalized_once_no_double_v1() -> None:
    # A trailing slash is stripped so the OpenAI client never builds /v1/v1.
    assert normalize_custom_base_url("http://host:4000/v1/") == "http://host:4000/v1"
    assert normalize_custom_base_url("  https://host/v1  ") == "https://host/v1"


def test_base_url_scheme_defaults_loopback_http_remote_https() -> None:
    assert normalize_custom_base_url("localhost:4000/v1") == "http://localhost:4000/v1"
    assert normalize_custom_base_url("127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert normalize_custom_base_url("proxy.example.com") == "https://proxy.example.com"


@pytest.mark.parametrize("provider", ["custom-openai", "custom-anthropic"])
def test_missing_base_url_is_rejected(provider: str) -> None:
    prefix = provider.replace("-", "_")
    with pytest.raises(ValueError, match="BASE_URL"):
        LLMSettings.model_validate({"provider": provider, f"{prefix}_reasoning_model": "m"})


@pytest.mark.parametrize("provider", ["custom-openai", "custom-anthropic"])
def test_missing_model_is_rejected(provider: str) -> None:
    prefix = provider.replace("-", "_")
    with pytest.raises(ValueError, match="requires a model"):
        LLMSettings.model_validate({"provider": provider, f"{prefix}_base_url": "https://host/v1"})


def test_per_tier_models_fall_back_to_custom_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://host/v1")
    monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "k")
    monkeypatch.setenv("CUSTOM_OPENAI_MODEL", "gpt-5.4")
    # The tier-specific vars must be unset for the fallback-to-MODEL path to be
    # exercised: a developer's local .env (or ambient env) may otherwise populate
    # them and mask the fallback this test asserts.
    monkeypatch.delenv("CUSTOM_OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_CLASSIFICATION_MODEL", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_TOOLCALL_MODEL", raising=False)
    from config.llm_settings import _llm_settings_env_payload

    s = LLMSettings.model_validate(_llm_settings_env_payload("custom-openai"))
    assert s.custom_openai_reasoning_model == "gpt-5.4"
    assert s.custom_openai_toolcall_model == "gpt-5.4"  # inherits CUSTOM_OPENAI_MODEL


# --------------------------------------------------------------------------- #
# custom-openai routing (both transports)
# --------------------------------------------------------------------------- #


def test_custom_openai_sdk_client_uses_user_base_url() -> None:
    client = build_reasoning_client(_route(_openai_settings()), "reasoning")
    assert isinstance(client, OpenAILLMClient)
    assert client._base_url == "http://localhost:4000/v1"
    assert client._api_key_env == "CUSTOM_OPENAI_API_KEY"
    assert client._model == "gpt-5.4"


def test_custom_openai_litellm_client_uses_user_base_url() -> None:
    client = build_litellm_llm_client(_openai_settings(), "custom-openai", "reasoning")
    assert client._api_base == "http://localhost:4000/v1"
    assert client._litellm_model == "openai/gpt-5.4"
    assert client._api_key_env == "CUSTOM_OPENAI_API_KEY"


def test_custom_openai_agent_client_uses_user_base_url() -> None:
    agent = build_agent_client(_route(_openai_settings()))
    assert isinstance(agent, OpenAIAgentClient)
    assert str(agent._client.base_url).rstrip("/") == "http://localhost:4000/v1"


def test_custom_openai_toolcall_fallback_stays_on_custom_endpoint() -> None:
    # HerShin5's ask: the reasoning client's toolcall fallback must resolve to the
    # per-tier toolcall model AND remain on the user's endpoint, never the default.
    reasoning = build_reasoning_client(_route(_openai_settings()), "reasoning")
    assert reasoning._model_fallback == "gpt-5.4-mini"
    toolcall = build_reasoning_client(_route(_openai_settings()), "toolcall")
    assert toolcall._model == "gpt-5.4-mini"
    assert toolcall._base_url == "http://localhost:4000/v1"


# --------------------------------------------------------------------------- #
# custom-anthropic (Anthropic SDK + base-URL override)
# --------------------------------------------------------------------------- #


def test_custom_anthropic_sync_client_threads_base_url_and_key() -> None:
    client = build_reasoning_client(_route(_anthropic_settings()), "reasoning")
    assert isinstance(client, LLMClient)
    assert client._base_url == "https://proxy.example.com"
    assert client._api_key_env == "CUSTOM_ANTHROPIC_API_KEY"
    assert client._model == "claude-opus-4-7"
    assert str(client._client.base_url).rstrip("/") == "https://proxy.example.com"


def test_custom_anthropic_agent_client_threads_base_url_and_key() -> None:
    agent = build_agent_client(_route(_anthropic_settings()))
    assert isinstance(agent, AnthropicAgentClient)
    assert agent.auth_error_hint == "Check CUSTOM_ANTHROPIC_API_KEY."
    assert str(agent._client.base_url).rstrip("/") == "https://proxy.example.com"


def test_custom_anthropic_does_not_borrow_anthropic_defaults() -> None:
    # The toolcall tier must use the custom model, not the anthropic_* default.
    client = build_reasoning_client(_route(_anthropic_settings()), "toolcall")
    assert client._model == "claude-haiku-4-5"
    assert client._api_key_env == "CUSTOM_ANTHROPIC_API_KEY"


@pytest.mark.parametrize("build", [build_litellm_agent_client, None])
def test_custom_anthropic_rejects_litellm_transport(build: object) -> None:
    settings = _anthropic_settings()
    with pytest.raises(RuntimeError, match="does not support OPENSRE_LLM_TRANSPORT=litellm"):
        if build is None:
            build_litellm_llm_client(settings, "custom-anthropic", "reasoning")
        else:
            build_litellm_agent_client(settings, "custom-anthropic")


# --------------------------------------------------------------------------- #
# Regression: first-party anthropic is unchanged
# --------------------------------------------------------------------------- #


def test_default_anthropic_still_uses_first_party_endpoint() -> None:
    settings = LLMSettings.model_validate({"provider": "anthropic"})
    client = build_reasoning_client(_route(settings), "reasoning")
    assert isinstance(client, LLMClient)
    assert client._base_url is None
    assert client._api_key_env == "ANTHROPIC_API_KEY"


# --------------------------------------------------------------------------- #
# Diagnostics redaction
# --------------------------------------------------------------------------- #


def test_redact_base_url_drops_path_and_query() -> None:
    assert redact_base_url("https://gw.example.com/v1/secret-token") == "https://gw.example.com"
    assert redact_base_url("http://localhost:4000/v1?key=abc") == "http://localhost:4000"
    assert redact_base_url("") == "(unset)"


# --------------------------------------------------------------------------- #
# custom-anthropic /v1 handling — the Anthropic SDK appends /v1/messages itself
# --------------------------------------------------------------------------- #


def test_anthropic_base_url_strips_trailing_v1() -> None:
    from config.constants.llm import normalize_anthropic_base_url

    # A trailing /v1 (the OpenAI convention) is stripped so the SDK's own
    # /v1/messages is not doubled; a deeper passthrough path is preserved.
    assert (
        normalize_anthropic_base_url("https://proxy.example.com/v1") == "https://proxy.example.com"
    )
    assert (
        normalize_anthropic_base_url("https://proxy.example.com/v1/") == "https://proxy.example.com"
    )
    assert normalize_anthropic_base_url("https://proxy.example.com/anthropic") == (
        "https://proxy.example.com/anthropic"
    )


def test_config_strips_v1_from_custom_anthropic_base_url() -> None:
    s = _anthropic_settings(custom_anthropic_base_url="https://proxy.example.com/v1")
    assert s.custom_anthropic_base_url == "https://proxy.example.com"


def test_custom_anthropic_client_never_doubles_v1() -> None:
    # End-to-end: a /v1-style base URL must not reach the SDK as /v1, or the
    # joined request URL becomes /v1/v1/messages (404).
    s = _anthropic_settings(custom_anthropic_base_url="https://proxy.example.com/v1")
    client = build_reasoning_client(_route(s), "reasoning")
    joined = str(client._client.base_url)
    assert "/v1/v1" not in joined
    assert joined.rstrip("/") == "https://proxy.example.com"


def test_redact_base_url_drops_userinfo() -> None:
    # A token embedded as userinfo must never reach the log.
    assert redact_base_url("https://s3cr3t-token@gw.example.com/v1") == "https://gw.example.com"
    assert redact_base_url("https://user:pass@host:8443/v1") == "https://host:8443"


def test_resolve_provider_models_returns_custom_model_not_default() -> None:
    from core.llm.provider_models import resolve_provider_models

    reasoning, toolcall = resolve_provider_models(_openai_settings(), "custom-openai")
    assert reasoning == "gpt-5.4"
    assert toolcall == "gpt-5.4-mini"


# --------------------------------------------------------------------------- #
# Chat-completions tool-arg coercion (found via the live demo: some gateways/
# models emit `arguments: "null"` for a no-arg tool, which must not become None)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw_arguments", ["null", '"a string"', "[1, 2]", "", "  "])
def test_parse_tool_calls_coerces_non_object_arguments_to_empty_dict(raw_arguments: str) -> None:
    from types import SimpleNamespace

    from core.llm.shared.openai_chat_completions import parse_tool_calls

    message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(name="get_status", arguments=raw_arguments),
            )
        ]
    )
    calls = parse_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].input == {}  # never None / never a non-dict


def test_parse_tool_calls_preserves_object_arguments() -> None:
    from types import SimpleNamespace

    from core.llm.shared.openai_chat_completions import parse_tool_calls

    message = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                id="c1",
                function=SimpleNamespace(name="q", arguments='{"region": "us-east-1"}'),
            )
        ]
    )
    assert parse_tool_calls(message)[0].input == {"region": "us-east-1"}
