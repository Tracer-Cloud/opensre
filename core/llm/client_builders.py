"""Construct the concrete LLM client for a resolved route.

Given an :class:`~core.llm.types.LLMRoute`, build the client for the transport
(CLI-backed, LiteLLM, or native vendor SDK) and provider the route resolved to.
``build_agent_client`` builds the tool-calling client; ``build_reasoning_client``
builds the streaming reasoning client for a model tier. The routing decision itself
lives in :mod:`core.llm.factory`; these functions only construct.

Import discipline: construction imports (``sdk`` / ``litellm`` / ``config``) are done
lazily inside functions, matching the rest of ``core.llm``. Keeping them lazy avoids
pulling the full provider stack at module import time and holds the ``importlinter``
contract.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.llm.types import AgentLLMClient, LLMRoute, ModelType

__all__ = ["build_agent_client", "build_reasoning_client"]


def _resolve_account_token(_env_name: str) -> str:
    """Adapt the account token resolver to the provider credential callback."""
    from config.account import resolve_account_token

    return resolve_account_token()


def _credit_actor_headers() -> dict[str, str]:
    """Name the member a hosted request is spent for, read per request (clients are shared)."""
    from config.credit_attribution import credit_actor_headers

    return credit_actor_headers()


# ---------------------------------------------------------------------------
# Tool-calling (agent) clients
# ---------------------------------------------------------------------------


def build_agent_client(route: LLMRoute) -> AgentLLMClient:
    """Build the tool-calling client for the route: CLI or LiteLLM transport, else native SDK."""
    if route.cli_provider_registration is not None:
        return _cli_agent_client(route.cli_provider_registration)

    if route.use_litellm:
        from core.llm.transports.litellm.routing import build_litellm_agent_client

        return build_litellm_agent_client(route.settings, route.provider)

    return _native_sdk_agent_client(route)


def _cli_agent_client(registration: Any) -> AgentLLMClient:
    """Build the subprocess CLI-backed tool-calling client for a CLI provider registration."""
    from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient

    model_name = os.getenv(registration.model_env_key, "").strip() or None
    return CLIBackedAgentClient(registration.adapter_factory(), model=model_name)


def _custom_anthropic_params(
    settings: Any, provider: str, model_type: ModelType
) -> tuple[str, str, int, str]:
    """Resolve ``(model, base_url, max_tokens, api_key_env)`` for a custom-anthropic client.

    Shared by the agent and reasoning builders so the resolution and the redacted
    per-build diagnostic live in exactly one place (one DEBUG line per build).
    """
    from config.constants.llm import CUSTOM_ANTHROPIC_API_KEY_ENV
    from config.llm_models import CUSTOM_ANTHROPIC_LLM_CONFIG
    from core.llm.providers.custom_endpoints import (
        custom_base_url,
        log_endpoint_resolution,
        select_custom_model,
    )

    model = select_custom_model(settings, provider, model_type)
    base_url = custom_base_url(settings, provider)
    log_endpoint_resolution(provider, base_url, model, model_type)
    return model, base_url, CUSTOM_ANTHROPIC_LLM_CONFIG.max_tokens, CUSTOM_ANTHROPIC_API_KEY_ENV


def _native_sdk_agent_client(route: LLMRoute) -> AgentLLMClient:
    """Build the native vendor-SDK tool-calling client for the route's provider."""
    from config.llm_settings import (
        PROVIDER_ANTHROPIC,
        PROVIDER_BEDROCK,
        PROVIDER_OLLAMA,
        PROVIDER_OPENAI,
    )
    from core.llm.providers.custom_endpoints import is_custom_anthropic_provider
    from core.llm.providers.openai_compat_providers import (
        is_openai_compat_provider,
        resolve_openai_compat_provider,
    )
    from core.llm.providers.provider_registry import FIRST_PARTY_PROVIDERS
    from core.llm.transports.sdk import agent_clients as sdk

    settings, provider = route.settings, route.provider

    if is_openai_compat_provider(provider):
        from core.llm.types import ModelType

        resolved = resolve_openai_compat_provider(settings, provider, ModelType.REASONING)
        max_tokens = 1024 if provider == PROVIDER_OLLAMA else resolved.config.max_tokens
        return sdk.OpenAIAgentClient(
            model=resolved.model,
            max_tokens=max_tokens,
            base_url=resolved.base_url,
            api_key_env=resolved.api_key_env,
            api_key_default=resolved.api_key_default,
        )

    if is_custom_anthropic_provider(provider):
        from core.llm.types import ModelType

        model, base_url, max_tokens, api_key_env = _custom_anthropic_params(
            settings, provider, ModelType.REASONING
        )
        return sdk.AnthropicAgentClient(
            model=model, max_tokens=max_tokens, base_url=base_url, api_key_env=api_key_env
        )

    spec = FIRST_PARTY_PROVIDERS.get(provider) or FIRST_PARTY_PROVIDERS[PROVIDER_ANTHROPIC]
    model = getattr(settings, f"{spec.env_prefix}_reasoning_model")
    if provider == PROVIDER_BEDROCK:
        from core.llm.providers.bedrock_model_ids import is_anthropic_bedrock_model

        if is_anthropic_bedrock_model(model):
            return sdk.BedrockAgentClient(model=model, max_tokens=spec.max_tokens)
        return sdk.BedrockConverseAgentClient(model=model, max_tokens=spec.max_tokens)

    if provider == PROVIDER_OPENAI:
        from config.account import account_llm_route

        account_route = account_llm_route()
        if account_route is not None:
            return sdk.OpenAIAgentClient(
                model=account_route.model,
                max_tokens=spec.max_tokens,
                base_url=account_route.base_url,
                credential_resolver=_resolve_account_token,
                request_headers=_credit_actor_headers,
            )
        return sdk.OpenAIAgentClient(model=model, max_tokens=spec.max_tokens)
    return sdk.AnthropicAgentClient(model=model, max_tokens=spec.max_tokens)


# ---------------------------------------------------------------------------
# Streaming reasoning clients
# ---------------------------------------------------------------------------


def build_reasoning_client(route: LLMRoute, model_type: ModelType) -> Any:
    """Build the reasoning client for the route and model tier: CLI or LiteLLM, else native SDK."""
    if route.cli_provider_registration is not None:
        return _cli_llm_client(route.cli_provider_registration, model_type)

    if route.use_litellm:
        from core.llm.shared.usage import emit_usage
        from core.llm.transports.litellm.routing import build_litellm_llm_client

        return build_litellm_llm_client(
            route.settings,
            route.provider,
            model_type,
            usage_callback=emit_usage,
        )

    return _native_sdk_llm_client(route, model_type)


def _cli_llm_client(registration: Any, model_type: ModelType) -> Any:
    """Build the subprocess CLI-backed reasoning client for a CLI provider registration."""
    from config.llm_models import DEFAULT_MAX_TOKENS
    from infrastructure.harness_providers import build_cli_client

    model_name = os.getenv(registration.model_env_key, "").strip() or None
    return build_cli_client(
        registration.adapter_factory(),
        model=model_name,
        max_tokens=DEFAULT_MAX_TOKENS,
        model_type=model_type,
    )


def _native_sdk_llm_client(route: LLMRoute, model_type: ModelType) -> Any:
    """Build the native vendor-SDK reasoning client for the route's provider and tier."""
    from config.llm_settings import PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_OPENAI
    from core.llm.providers.custom_endpoints import is_custom_anthropic_provider
    from core.llm.providers.openai_compat_providers import (
        is_openai_compat_provider,
        resolve_openai_compat_provider,
    )
    from core.llm.providers.provider_registry import FIRST_PARTY_PROVIDERS
    from core.llm.transports.sdk import llm_clients as sdk
    from core.llm.types import ModelType

    settings, provider = route.settings, route.provider

    def _fallback_model(provider_prefix: str) -> str | None:
        if model_type is ModelType.TOOLCALL:
            return None
        return str(getattr(settings, f"{provider_prefix}_toolcall_model", None) or "") or None

    if is_openai_compat_provider(provider):
        compat = resolve_openai_compat_provider(settings, provider, model_type)
        # Resolve the toolcall fallback via the compat path (settings_prefix
        # aware) rather than _fallback_model(provider): the raw slug can be
        # hyphenated (custom-openai), which getattr would never match.
        fallback_model: str | None = None
        if model_type != ModelType.TOOLCALL:
            toolcall = resolve_openai_compat_provider(settings, provider, ModelType.TOOLCALL)
            fallback_model = toolcall.model or None
        return sdk.OpenAILLMClient(
            model=compat.model,
            model_fallback=fallback_model,
            max_tokens=compat.config.max_tokens,
            base_url=compat.base_url,
            api_key_env=compat.api_key_env,
            api_key_default=compat.api_key_default,
            temperature=compat.temperature,
        )

    if is_custom_anthropic_provider(provider):
        model, base_url, max_tokens, api_key_env = _custom_anthropic_params(
            settings, provider, model_type
        )
        return sdk.LLMClient(
            model=model, max_tokens=max_tokens, base_url=base_url, api_key_env=api_key_env
        )

    spec = FIRST_PARTY_PROVIDERS.get(provider) or FIRST_PARTY_PROVIDERS[PROVIDER_ANTHROPIC]
    model = str(getattr(settings, f"{spec.env_prefix}_{model_type}_model"))
    if provider == PROVIDER_OPENAI:
        from config.account import account_llm_route

        account_route = account_llm_route()
        if account_route is not None:
            return sdk.OpenAILLMClient(
                model=account_route.model,
                max_tokens=spec.max_tokens,
                base_url=account_route.base_url,
                credential_resolver=_resolve_account_token,
                request_headers=_credit_actor_headers,
            )
        return sdk.OpenAILLMClient(
            model=model,
            model_fallback=_fallback_model("openai"),
            max_tokens=spec.max_tokens,
        )
    if provider == PROVIDER_BEDROCK:
        return sdk.BedrockLLMClient(model=model, max_tokens=spec.max_tokens)
    return sdk.LLMClient(model=model, max_tokens=spec.max_tokens)
