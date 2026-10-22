"""Per-provider LiteLLM model, credentials, and base-URL resolution.

Maps each API provider (anthropic, openai, bedrock, openai-compat) to the
correct LiteLLM model prefix, credential env var, and optional ``api_base``
so the dispatch entrypoints can build a :class:`~core.llm.transports.litellm.clients.LiteLLMAgentClient`
or :class:`~core.llm.transports.litellm.clients.LiteLLMLLMClient` without embedding
provider-specific knowledge.
"""

from __future__ import annotations

from typing import Any

from core.llm.providers.azure_openai import (
    is_azure_openai_provider,
    resolve_azure_openai_request_kwargs,
)
from core.llm.providers.custom_endpoints import is_custom_anthropic_provider
from core.llm.providers.openai_compat_providers import (
    is_openai_compat_provider,
    resolve_openai_compat_provider,
)
from core.llm.providers.vertex_ai import (
    is_vertex_ai_provider,
    resolve_vertex_ai_request_kwargs,
)
from core.llm.transports.litellm.clients import LiteLLMAgentClient, LiteLLMLLMClient
from core.llm.transports.litellm.responses import (
    ChatGPTResponsesAgentClient,
    ChatGPTResponsesLLMClient,
)
from core.llm.types import ModelType


def _guard_sdk_only_provider(provider: str) -> None:
    """Reject providers that are intentionally SDK-only under the LiteLLM transport."""
    if is_custom_anthropic_provider(provider):
        raise RuntimeError(
            "Provider 'custom-anthropic' uses the Anthropic SDK with a base-URL override "
            "and does not support OPENSRE_LLM_TRANSPORT=litellm. Unset OPENSRE_LLM_TRANSPORT "
            "(or set it to 'sdk'), or use 'custom-openai' for a LiteLLM-proxied "
            "OpenAI-compatible endpoint."
        )


def _litellm_model_for_compat(model: str) -> str:
    """Prefix model with ``openai/`` if not already prefixed, for compat endpoints."""
    return model if model.startswith("openai/") else f"openai/{model}"


def build_litellm_agent_client(
    settings: Any, provider: str
) -> LiteLLMAgentClient | ChatGPTResponsesAgentClient:
    """Build a :class:`LiteLLMAgentClient` for the given provider and settings."""
    if provider == "chatgpt":
        from core.llm.provider_models import resolve_provider_models

        return ChatGPTResponsesAgentClient(resolve_provider_models(settings, provider)[0])
    _guard_sdk_only_provider(provider)
    from core.llm.providers.provider_registry import FIRST_PARTY_PROVIDERS

    spec = FIRST_PARTY_PROVIDERS.get(provider)
    if spec is not None:
        model = getattr(settings, f"{spec.env_prefix}_reasoning_model")
        return LiteLLMAgentClient(
            litellm_model=f"{spec.litellm_prefix}/{model}",
            max_tokens=spec.max_tokens,
            api_key_env=spec.api_key_env,
        )

    if is_azure_openai_provider(provider):
        from config.llm_models import AZURE_OPENAI_LLM_CONFIG

        azure = resolve_azure_openai_request_kwargs(settings, model_type=ModelType.REASONING)
        return LiteLLMAgentClient(
            litellm_model=azure["litellm_model"],
            max_tokens=AZURE_OPENAI_LLM_CONFIG.max_tokens,
            api_base=azure["api_base"],
            api_version=azure["api_version"],
            api_key_env=azure["api_key_env"],
        )

    if is_openai_compat_provider(provider):
        from config.llm_settings import PROVIDER_OLLAMA

        resolved = resolve_openai_compat_provider(settings, provider, ModelType.REASONING)
        max_tokens = 1024 if provider == PROVIDER_OLLAMA else resolved.config.max_tokens
        return LiteLLMAgentClient(
            litellm_model=_litellm_model_for_compat(resolved.model),
            max_tokens=max_tokens,
            api_base=resolved.base_url,
            api_key_env=resolved.api_key_env,
            api_key_default=resolved.api_key_default,
            temperature=resolved.temperature,
        )

    if is_vertex_ai_provider(provider):
        from config.llm_models import VERTEX_AI_LLM_CONFIG

        vertex = resolve_vertex_ai_request_kwargs(settings, model_type=ModelType.REASONING)
        return LiteLLMAgentClient(
            litellm_model=vertex["litellm_model"],
            max_tokens=VERTEX_AI_LLM_CONFIG.max_tokens,
            vertex_project=vertex.get("vertex_project"),
            vertex_location=vertex.get("vertex_location"),
            api_key_env=None,
        )

    raise RuntimeError(
        f"No LiteLLM routing configured for provider '{provider}'. "
        "Use OPENSRE_LLM_TRANSPORT=sdk or add routing support for this provider."
    )


def build_litellm_llm_client(
    settings: Any,
    provider: str,
    model_type: ModelType,
    *,
    usage_callback: Any = None,
) -> LiteLLMLLMClient | ChatGPTResponsesLLMClient:
    """Build a :class:`LiteLLMLLMClient` for the given provider, model tier, and settings."""
    if provider == "chatgpt":
        from core.llm.provider_models import resolve_provider_models

        return ChatGPTResponsesLLMClient(
            resolve_provider_models(settings, provider)[0],
            usage_callback=usage_callback,
        )
    _guard_sdk_only_provider(provider)

    from core.llm.providers.provider_registry import FIRST_PARTY_PROVIDERS

    def _fallback(provider_prefix: str) -> str | None:
        if model_type is ModelType.TOOLCALL:
            return None
        attr = f"{provider_prefix}_toolcall_model"
        return str(getattr(settings, attr, None) or "")

    spec = FIRST_PARTY_PROVIDERS.get(provider)
    if spec is not None:
        model = str(getattr(settings, f"{spec.env_prefix}_{model_type}_model"))
        fallback = _fallback(spec.env_prefix)
        return LiteLLMLLMClient(
            litellm_model=f"{spec.litellm_prefix}/{model}",
            model_fallback=(fallback and f"{spec.litellm_prefix}/{fallback}") or None,
            max_tokens=spec.max_tokens,
            api_key_env=spec.api_key_env,
            usage_callback=usage_callback,
        )

    if is_azure_openai_provider(provider):
        from config.llm_models import AZURE_OPENAI_LLM_CONFIG

        azure = resolve_azure_openai_request_kwargs(settings, model_type=model_type)
        raw_fallback = _fallback("azure_openai")
        azure_fallback_model: str | None = None
        if raw_fallback:
            azure_fallback_model = (
                raw_fallback if raw_fallback.startswith("azure/") else f"azure/{raw_fallback}"
            )
        return LiteLLMLLMClient(
            litellm_model=azure["litellm_model"],
            model_fallback=azure_fallback_model,
            max_tokens=AZURE_OPENAI_LLM_CONFIG.max_tokens,
            api_base=azure["api_base"],
            api_version=azure["api_version"],
            api_key_env=azure["api_key_env"],
            usage_callback=usage_callback,
        )

    if is_openai_compat_provider(provider):
        compat = resolve_openai_compat_provider(settings, provider, model_type)
        # Resolve the toolcall fallback via the compat path (settings_prefix
        # aware) rather than _fallback(provider): the raw slug can be hyphenated
        # (custom-openai), which getattr would never match.
        fallback_model: str | None = None
        if model_type != ModelType.TOOLCALL:
            fallback_compat = resolve_openai_compat_provider(settings, provider, ModelType.TOOLCALL)
            if fallback_compat.model:
                fallback_model = _litellm_model_for_compat(fallback_compat.model)
        return LiteLLMLLMClient(
            litellm_model=_litellm_model_for_compat(compat.model),
            model_fallback=fallback_model,
            max_tokens=compat.config.max_tokens,
            api_base=compat.base_url,
            api_key_env=compat.api_key_env,
            api_key_default=compat.api_key_default,
            temperature=compat.temperature,
            usage_callback=usage_callback,
        )

    if is_vertex_ai_provider(provider):
        from config.llm_models import VERTEX_AI_LLM_CONFIG

        vertex = resolve_vertex_ai_request_kwargs(settings, model_type=model_type)
        raw_fallback = _fallback("vertex_ai")
        vertex_fallback_model = f"vertex_ai/{raw_fallback}" if raw_fallback else None
        return LiteLLMLLMClient(
            litellm_model=vertex["litellm_model"],
            model_fallback=vertex_fallback_model,
            max_tokens=VERTEX_AI_LLM_CONFIG.max_tokens,
            vertex_project=vertex.get("vertex_project"),
            vertex_location=vertex.get("vertex_location"),
            api_key_env=None,
            usage_callback=usage_callback,
        )

    raise RuntimeError(
        f"No LiteLLM routing configured for provider '{provider}'. "
        "Use OPENSRE_LLM_TRANSPORT=sdk or add routing support for this provider."
    )
