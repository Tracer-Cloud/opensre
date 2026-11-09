"""OpenAI-compatible provider catalog and runtime model/base-URL resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from config.constants.llm import CUSTOM_OPENAI_API_KEY_ENV, TRUSTEDROUTER_API_KEY_ENV
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
