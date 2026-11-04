"""OpenAI-compatible provider catalog and runtime model/base-URL resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final

from config.constants.llm import (
    CUSTOM_OPENAI_API_KEY_ENV,
    LLM_MAX_TOKENS_ENV,
    TRUSTEDROUTER_API_KEY_ENV,
)
from config.llm_models import (
    CUSTOM_OPENAI_LLM_CONFIG,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_LLM_CONFIG,
    GEMINI_BASE_URL,
    GEMINI_LLM_CONFIG,
    GROQ_BASE_URL,
    GROQ_LLM_CONFIG,
    MINIMAX_BASE_URL,
    MINIMAX_LLM_CONFIG,
    NVIDIA_BASE_URL,
    NVIDIA_LLM_CONFIG,
    OLLAMA_LLM_CONFIG,
    OPENROUTER_BASE_URL,
    OPENROUTER_LLM_CONFIG,
    TRUSTEDROUTER_BASE_URL,
    TRUSTEDROUTER_LLM_CONFIG,
    LLMModelConfig,
)
from core.llm.types import ModelType


@dataclass(frozen=True)
class OpenAICompatProvider:
    """Static construction data for an OpenAI-compatible provider."""

    config: LLMModelConfig
    base_url: str | None
    api_key_env: str
    settings_prefix: str
    temperature: float | None = None
    api_key_default: str = ""
    #: Env var that lets agent clients override this provider's conservative
    #: output-token default (e.g. a local model that also bridges to a larger
    #: served one). ``None`` keeps the configured limit.
    agent_max_tokens_env: str | None = None


@dataclass(frozen=True)
class ResolvedOpenAICompatProvider:
    """Provider data after applying runtime settings such as model tier and host."""

    name: str
    model: str
    config: LLMModelConfig
    base_url: str
    api_key_env: str
    temperature: float | None = None
    api_key_default: str = ""


OPENAI_COMPATIBLE_PROVIDERS: Final[dict[str, OpenAICompatProvider]] = {
    "openrouter": OpenAICompatProvider(
        OPENROUTER_LLM_CONFIG,
        OPENROUTER_BASE_URL,
        "OPENROUTER_API_KEY",
        "openrouter",
    ),
    "trustedrouter": OpenAICompatProvider(
        TRUSTEDROUTER_LLM_CONFIG,
        TRUSTEDROUTER_BASE_URL,
        TRUSTEDROUTER_API_KEY_ENV,
        "trustedrouter",
    ),
    "deepseek": OpenAICompatProvider(
        DEEPSEEK_LLM_CONFIG,
        DEEPSEEK_BASE_URL,
        "DEEPSEEK_API_KEY",
        "deepseek",
    ),
    "gemini": OpenAICompatProvider(
        GEMINI_LLM_CONFIG,
        GEMINI_BASE_URL,
        "GEMINI_API_KEY",
        "gemini",
    ),
    "nvidia": OpenAICompatProvider(
        NVIDIA_LLM_CONFIG,
        NVIDIA_BASE_URL,
        "NVIDIA_API_KEY",
        "nvidia",
    ),
    "minimax": OpenAICompatProvider(
        MINIMAX_LLM_CONFIG,
        MINIMAX_BASE_URL,
        "MINIMAX_API_KEY",
        "minimax",
        temperature=1.0,
    ),
    "groq": OpenAICompatProvider(
        GROQ_LLM_CONFIG,
        GROQ_BASE_URL,
        "GROQ_API_KEY",
        "groq",
    ),
    "ollama": OpenAICompatProvider(
        OLLAMA_LLM_CONFIG,
        None,
        "OLLAMA_API_KEY",
        "ollama",
        api_key_default="ollama",
        agent_max_tokens_env=LLM_MAX_TOKENS_ENV,
    ),
    "custom-openai": OpenAICompatProvider(
        CUSTOM_OPENAI_LLM_CONFIG,
        None,
        CUSTOM_OPENAI_API_KEY_ENV,
        "custom_openai",
    ),
}


def is_openai_compat_provider(provider: str) -> bool:
    """Return whether *provider* is handled by the OpenAI-compatible boundary."""
    return provider in OPENAI_COMPATIBLE_PROVIDERS


def select_compat_model(settings: Any, provider: str, model_type: ModelType) -> str:
    """Select the configured model for *provider* and *model_type*."""
    if provider == "ollama":
        return str(settings.ollama_model)
    prefix = OPENAI_COMPATIBLE_PROVIDERS[provider].settings_prefix
    attr = f"{prefix}_{model_type}_model"
    return str(getattr(settings, attr))


_DEFAULT_AGENT_MAX_TOKENS: Final = 1024


def _env_capped_max_tokens(env_name: str) -> int:
    """Output-token cap read from ``env_name``, with a conservative default.

    For a provider whose own default is deliberately low but may bridge to a
    larger served model: the env override lifts the cap, an unset or invalid
    value keeps the default.
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return _DEFAULT_AGENT_MAX_TOKENS
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_AGENT_MAX_TOKENS


def agent_max_tokens(resolved: ResolvedOpenAICompatProvider) -> int:
    """Output-token cap for a tool-calling agent client.

    A provider whose spec names an ``agent_max_tokens_env`` lets agent clients
    lift its conservative default through that env var; others keep their
    configured limit. Non-agent clients never call this, so their limits stand.
    """
    agent_max_tokens_env = OPENAI_COMPATIBLE_PROVIDERS[resolved.name].agent_max_tokens_env
    if agent_max_tokens_env is None:
        return resolved.config.max_tokens
    return _env_capped_max_tokens(agent_max_tokens_env)


def resolve_openai_compat_provider(
    settings: Any,
    provider: str,
    model_type: ModelType,
) -> ResolvedOpenAICompatProvider:
    """Resolve static provider metadata plus runtime model/base-url settings."""
    spec = OPENAI_COMPATIBLE_PROVIDERS[provider]
    base_url = spec.base_url
    if provider == "ollama":
        base_url = f"{settings.ollama_host.rstrip('/')}/v1"
    elif provider == "custom-openai":
        base_url = settings.custom_openai_base_url
    if not base_url:
        raise RuntimeError(f"OpenAI-compatible provider '{provider}' is missing a base URL.")
    model = select_compat_model(settings, provider, model_type)
    if provider == "custom-openai":
        from core.llm.providers.custom_endpoints import log_endpoint_resolution

        log_endpoint_resolution(provider, base_url, model, model_type)
    return ResolvedOpenAICompatProvider(
        name=provider,
        model=model,
        config=spec.config,
        base_url=base_url,
        api_key_env=spec.api_key_env,
        temperature=spec.temperature,
        api_key_default=spec.api_key_default,
    )
