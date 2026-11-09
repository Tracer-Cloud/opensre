"""Tests for ``core.llm.client_builders`` — the registry-driven client construction.

Each first-party provider (anthropic, openai, bedrock) must build its agent and
reasoning clients from ``FIRST_PARTY_PROVIDERS`` for both the native SDK and the
LiteLLM transport, and Bedrock must still pick the Anthropic vs Converse client
by model id.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.llm.client_builders import build_agent_client, build_reasoning_client
from core.llm.providers.provider_registry import FIRST_PARTY_PROVIDERS
from core.llm.types import LLMRoute


@pytest.fixture(autouse=True)
def _provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK clients validate credentials/region at construction time."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")


def _settings(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider,
        anthropic_reasoning_model="claude-r",
        anthropic_classification_model="claude-c",
        anthropic_toolcall_model="claude-t",
        openai_reasoning_model="gpt-r",
        openai_classification_model="gpt-c",
        openai_toolcall_model="gpt-t",
        bedrock_reasoning_model="us.anthropic.claude-r",
        bedrock_classification_model="us.anthropic.claude-c",
        bedrock_toolcall_model="us.anthropic.claude-t",
    )


def _route(provider: str, *, use_litellm: bool = False) -> LLMRoute:
    return LLMRoute(
        settings=_settings(provider),
        provider=provider,
        cli_provider_registration=None,
        use_litellm=use_litellm,
    )


def test_registry_lists_the_three_first_party_providers() -> None:
    assert set(FIRST_PARTY_PROVIDERS) == {"anthropic", "openai", "bedrock"}


@pytest.mark.parametrize(
    ("provider", "agent_class", "reasoning_class"),
    [
        ("anthropic", "AnthropicAgentClient", "LLMClient"),
        ("openai", "OpenAIAgentClient", "OpenAILLMClient"),
        ("bedrock", "BedrockAgentClient", "BedrockLLMClient"),
    ],
)
def test_native_sdk_clients_built_via_registry(
    provider: str, agent_class: str, reasoning_class: str
) -> None:
    assert type(build_agent_client(_route(provider))).__name__ == agent_class
    assert type(build_reasoning_client(_route(provider), "reasoning")).__name__ == reasoning_class


def test_openai_clients_use_account_proxy_when_personal_login_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def account_route() -> SimpleNamespace:
        return SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1",
            model="gpt-account",
        )

    def account_token() -> str:
        return "osre_pat_test"

    monkeypatch.setattr("config.account.account_llm_route", account_route)
    monkeypatch.setattr("config.account.resolve_account_token", account_token)

    agent = build_agent_client(_route("openai"))
    reasoning = build_reasoning_client(_route("openai"), "reasoning")

    assert agent.model_id == "gpt-account"
    assert str(agent._client.base_url) == "https://app.opensre.com/api/llm/v1/"
    assert reasoning._model == "gpt-account"
    assert reasoning._base_url == "https://app.opensre.com/api/llm/v1"


def test_openai_agent_default_output_budget_reaches_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory's reasoning budget reaches the actual Responses request."""
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)
    route = _route("openai")
    route.settings.openai_reasoning_model = "gpt-5.6-sol"
    agent = build_agent_client(route)
    captured: dict[str, Any] = {}

    def create(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(output=[], output_text="B. The constraints require it.", usage=None)

    monkeypatch.setattr(agent._client.responses, "create", create)

    agent.invoke([{"role": "user", "content": "Choose the correct letter."}])

    assert captured["max_output_tokens"] == 25_000


@pytest.mark.parametrize("provider", ["anthropic", "openai", "bedrock"])
def test_litellm_clients_built_via_registry(provider: str) -> None:
    route = _route(provider, use_litellm=True)
    assert type(build_agent_client(route)).__name__ == "LiteLLMAgentClient"
    assert type(build_reasoning_client(route, "reasoning")).__name__ == "LiteLLMLLMClient"


def test_bedrock_picks_converse_client_for_non_anthropic_model() -> None:
    settings = _settings("bedrock")
    settings.bedrock_reasoning_model = "amazon.titan-text"
    route = LLMRoute(
        settings=settings, provider="bedrock", cli_provider_registration=None, use_litellm=False
    )
    assert type(build_agent_client(route)).__name__ == "BedrockConverseAgentClient"


def test_ollama_sdk_reasoning_builds_without_per_tier_toolcall_attr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: Ollama stores a flat ``ollama_model`` key (no per-tier toolcall
    attribute), so the SDK reasoning fallback lookup must default rather than raise
    ``AttributeError`` — mirroring the defensive lookup on the LiteLLM path."""
    from core.llm.factory import build_llm_client

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OPENSRE_LLM_TRANSPORT", raising=False)

    # reasoning + classification are the non-toolcall tiers that hit the fallback lookup.
    assert type(build_llm_client("reasoning")).__name__ == "OpenAILLMClient"
    assert type(build_llm_client("classification")).__name__ == "OpenAILLMClient"
