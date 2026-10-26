"""Canonical LLM model defaults and base URLs."""

from __future__ import annotations

from dataclasses import dataclass

from config.strict_config import StrictConfigModel


class LLMModelConfig(StrictConfigModel):
    """Configuration for an LLM provider's model variants.

    Three tiers, ordered by capability/cost:
    - ``reasoning_model`` — highest-capability model used for root-cause
      diagnosis and other deep-reasoning steps (e.g. Claude Opus, GPT-5).
    - ``classification_model`` — mid-tier model for tasks that need more
      reasoning than a fast toolcall model but don't justify reasoning cost
      (e.g. interactive-shell intent classification). Sonnet for Anthropic.
    - ``toolcall_model`` — lightweight, low-latency model for simple tool
      selection / action planning (e.g. Claude Haiku, GPT-5 mini).
    """

    reasoning_model: str
    classification_model: str
    toolcall_model: str
    max_tokens: int


@dataclass(frozen=True)
class ProviderModelDefaults:
    """Static model-tier defaults for one hosted LLM provider."""

    provider: str
    settings_key: str
    reasoning: str
    classification: str
    toolcall: str
    base_url: str | None = None
    single_model_settings: bool = False
    requires_explicit_models: bool = False


DEFAULT_MAX_TOKENS = 25_000
DEFAULT_AZURE_OPENAI_API_VERSION = "2024-10-21"
DEFAULT_VERTEX_AI_LOCATION = "us-central1"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TRUSTEDROUTER_BASE_URL = "https://api.trustedrouter.com/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

PROVIDER_MODEL_DEFAULTS: dict[str, ProviderModelDefaults] = {
    "anthropic": ProviderModelDefaults(
        provider="anthropic",
        settings_key="anthropic",
        reasoning="claude-opus-4-7",
        classification="claude-sonnet-4-6",
        toolcall="claude-haiku-4-5-20251001",
    ),
    "openai": ProviderModelDefaults(
        provider="openai",
        settings_key="openai",
        reasoning="gpt-5.4-mini",
        classification="gpt-5.4-mini",
        toolcall="gpt-5.4-mini",
    ),
    "openrouter": ProviderModelDefaults(
        provider="openrouter",
        settings_key="openrouter",
        reasoning="openrouter/auto",
        classification="openrouter/auto",
        toolcall="openrouter/auto",
        base_url=OPENROUTER_BASE_URL,
    ),
    "trustedrouter": ProviderModelDefaults(
        provider="trustedrouter",
        settings_key="trustedrouter",
        reasoning="trustedrouter/auto",
        classification="trustedrouter/auto",
        toolcall="trustedrouter/auto",
        base_url=TRUSTEDROUTER_BASE_URL,
    ),
    "deepseek": ProviderModelDefaults(
        provider="deepseek",
        settings_key="deepseek",
        reasoning="deepseek-v4-pro",
        classification="deepseek-v4-flash",
        toolcall="deepseek-v4-flash",
        base_url=DEEPSEEK_BASE_URL,
    ),
    "gemini": ProviderModelDefaults(
        provider="gemini",
        settings_key="gemini",
        reasoning="gemini-3.1-pro-preview",
        classification="gemini-3-flash-preview",
        toolcall="gemini-3.1-flash-lite-preview",
        base_url=GEMINI_BASE_URL,
    ),
    "nvidia": ProviderModelDefaults(
        provider="nvidia",
        settings_key="nvidia",
        reasoning="meta/llama-3.1-405b-instruct",
        classification="meta/llama-3.1-70b-instruct",
        toolcall="meta/llama-3.1-8b-instruct",
        base_url=NVIDIA_BASE_URL,
    ),
    "minimax": ProviderModelDefaults(
        provider="minimax",
        settings_key="minimax",
        reasoning="MiniMax-M3",
        classification="MiniMax-M2.7-highspeed",
        toolcall="MiniMax-M2.7-highspeed",
        base_url=MINIMAX_BASE_URL,
    ),
    "groq": ProviderModelDefaults(
        provider="groq",
        settings_key="groq",
        reasoning="llama-3.3-70b-versatile",
        classification="llama-3.3-70b-versatile",
        toolcall="llama-3.1-8b-instant",
        base_url=GROQ_BASE_URL,
    ),
    "azure-openai": ProviderModelDefaults(
        provider="azure-openai",
        settings_key="azure_openai",
        reasoning="gpt-5.4-mini",
        classification="gpt-5.4-mini",
        toolcall="gpt-5.4-mini",
    ),
    "custom-openai": ProviderModelDefaults(
        provider="custom-openai",
        settings_key="custom_openai",
        reasoning="",
        classification="",
        toolcall="",
        requires_explicit_models=True,
    ),
    "custom-anthropic": ProviderModelDefaults(
        provider="custom-anthropic",
        settings_key="custom_anthropic",
        reasoning="",
        classification="",
        toolcall="",
        requires_explicit_models=True,
    ),
    "bedrock": ProviderModelDefaults(
        provider="bedrock",
        settings_key="bedrock",
        reasoning="us.anthropic.claude-sonnet-4-6",
        classification="us.anthropic.claude-sonnet-4-6",
        toolcall="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ),
    "vertex-ai": ProviderModelDefaults(
        provider="vertex-ai",
        settings_key="vertex_ai",
        reasoning="gemini-2.5-pro",
        classification="gemini-2.5-flash",
        toolcall="gemini-2.5-flash-lite",
    ),
    "ollama": ProviderModelDefaults(
        provider="ollama",
        settings_key="ollama",
        reasoning="llama3.2",
        classification="llama3.2",
        toolcall="llama3.2",
        single_model_settings=True,
    ),
}


def model_config_for(provider: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> LLMModelConfig:
    """Return an ``LLMModelConfig`` built from the catalog row for *provider*."""
    defaults = PROVIDER_MODEL_DEFAULTS[provider]
    return LLMModelConfig(
        reasoning_model=defaults.reasoning,
        classification_model=defaults.classification,
        toolcall_model=defaults.toolcall,
        max_tokens=max_tokens,
    )


def _defaults(provider: str) -> ProviderModelDefaults:
    return PROVIDER_MODEL_DEFAULTS[provider]


ANTHROPIC_REASONING_MODEL = _defaults("anthropic").reasoning
ANTHROPIC_CLASSIFICATION_MODEL = _defaults("anthropic").classification
ANTHROPIC_TOOLCALL_MODEL = _defaults("anthropic").toolcall

OPENAI_REASONING_MODEL = _defaults("openai").reasoning
OPENAI_CLASSIFICATION_MODEL = _defaults("openai").classification
OPENAI_TOOLCALL_MODEL = _defaults("openai").toolcall

OPENROUTER_REASONING_MODEL = _defaults("openrouter").reasoning
OPENROUTER_CLASSIFICATION_MODEL = _defaults("openrouter").classification
OPENROUTER_TOOLCALL_MODEL = _defaults("openrouter").toolcall

TRUSTEDROUTER_REASONING_MODEL = _defaults("trustedrouter").reasoning
TRUSTEDROUTER_CLASSIFICATION_MODEL = _defaults("trustedrouter").classification
TRUSTEDROUTER_TOOLCALL_MODEL = _defaults("trustedrouter").toolcall

DEEPSEEK_REASONING_MODEL = _defaults("deepseek").reasoning
DEEPSEEK_CLASSIFICATION_MODEL = _defaults("deepseek").classification
DEEPSEEK_TOOLCALL_MODEL = _defaults("deepseek").toolcall

GEMINI_REASONING_MODEL = _defaults("gemini").reasoning
GEMINI_CLASSIFICATION_MODEL = _defaults("gemini").classification
GEMINI_TOOLCALL_MODEL = _defaults("gemini").toolcall

NVIDIA_REASONING_MODEL = _defaults("nvidia").reasoning
NVIDIA_CLASSIFICATION_MODEL = _defaults("nvidia").classification
NVIDIA_TOOLCALL_MODEL = _defaults("nvidia").toolcall

MINIMAX_REASONING_MODEL = _defaults("minimax").reasoning
MINIMAX_CLASSIFICATION_MODEL = _defaults("minimax").classification
MINIMAX_TOOLCALL_MODEL = _defaults("minimax").toolcall

GROQ_REASONING_MODEL = _defaults("groq").reasoning
GROQ_CLASSIFICATION_MODEL = _defaults("groq").classification
GROQ_TOOLCALL_MODEL = _defaults("groq").toolcall

AZURE_OPENAI_REASONING_MODEL = _defaults("azure-openai").reasoning
AZURE_OPENAI_CLASSIFICATION_MODEL = _defaults("azure-openai").classification
AZURE_OPENAI_TOOLCALL_MODEL = _defaults("azure-openai").toolcall

CUSTOM_OPENAI_REASONING_MODEL = _defaults("custom-openai").reasoning
CUSTOM_OPENAI_CLASSIFICATION_MODEL = _defaults("custom-openai").classification
CUSTOM_OPENAI_TOOLCALL_MODEL = _defaults("custom-openai").toolcall

CUSTOM_ANTHROPIC_REASONING_MODEL = _defaults("custom-anthropic").reasoning
CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL = _defaults("custom-anthropic").classification
CUSTOM_ANTHROPIC_TOOLCALL_MODEL = _defaults("custom-anthropic").toolcall

BEDROCK_REASONING_MODEL = _defaults("bedrock").reasoning
BEDROCK_CLASSIFICATION_MODEL = _defaults("bedrock").classification
BEDROCK_TOOLCALL_MODEL = _defaults("bedrock").toolcall

VERTEX_AI_REASONING_MODEL = _defaults("vertex-ai").reasoning
VERTEX_AI_CLASSIFICATION_MODEL = _defaults("vertex-ai").classification
VERTEX_AI_TOOLCALL_MODEL = _defaults("vertex-ai").toolcall

DEFAULT_OLLAMA_MODEL = _defaults("ollama").reasoning

ANTHROPIC_LLM_CONFIG = model_config_for("anthropic")
OPENAI_LLM_CONFIG = model_config_for("openai")
OPENROUTER_LLM_CONFIG = model_config_for("openrouter")
TRUSTEDROUTER_LLM_CONFIG = model_config_for("trustedrouter")
DEEPSEEK_LLM_CONFIG = model_config_for("deepseek")
GROQ_LLM_CONFIG = model_config_for("groq")
AZURE_OPENAI_LLM_CONFIG = model_config_for("azure-openai")
CUSTOM_OPENAI_LLM_CONFIG = model_config_for("custom-openai")
CUSTOM_ANTHROPIC_LLM_CONFIG = model_config_for("custom-anthropic")
GEMINI_LLM_CONFIG = model_config_for("gemini")
NVIDIA_LLM_CONFIG = model_config_for("nvidia")
MINIMAX_LLM_CONFIG = model_config_for("minimax")
BEDROCK_LLM_CONFIG = model_config_for("bedrock")
VERTEX_AI_LLM_CONFIG = model_config_for("vertex-ai")
OLLAMA_LLM_CONFIG = model_config_for("ollama")

__all__ = [
    "ANTHROPIC_CLASSIFICATION_MODEL",
    "ANTHROPIC_LLM_CONFIG",
    "ANTHROPIC_REASONING_MODEL",
    "ANTHROPIC_TOOLCALL_MODEL",
    "AZURE_OPENAI_CLASSIFICATION_MODEL",
    "AZURE_OPENAI_LLM_CONFIG",
    "AZURE_OPENAI_REASONING_MODEL",
    "AZURE_OPENAI_TOOLCALL_MODEL",
    "BEDROCK_CLASSIFICATION_MODEL",
    "BEDROCK_LLM_CONFIG",
    "BEDROCK_REASONING_MODEL",
    "BEDROCK_TOOLCALL_MODEL",
    "CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL",
    "CUSTOM_ANTHROPIC_LLM_CONFIG",
    "CUSTOM_ANTHROPIC_REASONING_MODEL",
    "CUSTOM_ANTHROPIC_TOOLCALL_MODEL",
    "CUSTOM_OPENAI_CLASSIFICATION_MODEL",
    "CUSTOM_OPENAI_LLM_CONFIG",
    "CUSTOM_OPENAI_REASONING_MODEL",
    "CUSTOM_OPENAI_TOOLCALL_MODEL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_CLASSIFICATION_MODEL",
    "DEEPSEEK_LLM_CONFIG",
    "DEEPSEEK_REASONING_MODEL",
    "DEEPSEEK_TOOLCALL_MODEL",
    "DEFAULT_AZURE_OPENAI_API_VERSION",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OLLAMA_HOST",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_VERTEX_AI_LOCATION",
    "GEMINI_BASE_URL",
    "GEMINI_CLASSIFICATION_MODEL",
    "GEMINI_LLM_CONFIG",
    "GEMINI_REASONING_MODEL",
    "GEMINI_TOOLCALL_MODEL",
    "GROQ_BASE_URL",
    "GROQ_CLASSIFICATION_MODEL",
    "GROQ_LLM_CONFIG",
    "GROQ_REASONING_MODEL",
    "GROQ_TOOLCALL_MODEL",
    "LLMModelConfig",
    "MINIMAX_BASE_URL",
    "MINIMAX_CLASSIFICATION_MODEL",
    "MINIMAX_LLM_CONFIG",
    "MINIMAX_REASONING_MODEL",
    "MINIMAX_TOOLCALL_MODEL",
    "NVIDIA_BASE_URL",
    "NVIDIA_CLASSIFICATION_MODEL",
    "NVIDIA_LLM_CONFIG",
    "NVIDIA_REASONING_MODEL",
    "NVIDIA_TOOLCALL_MODEL",
    "OLLAMA_LLM_CONFIG",
    "OPENAI_CLASSIFICATION_MODEL",
    "OPENAI_LLM_CONFIG",
    "OPENAI_REASONING_MODEL",
    "OPENAI_TOOLCALL_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_CLASSIFICATION_MODEL",
    "OPENROUTER_LLM_CONFIG",
    "OPENROUTER_REASONING_MODEL",
    "OPENROUTER_TOOLCALL_MODEL",
    "PROVIDER_MODEL_DEFAULTS",
    "ProviderModelDefaults",
    "TRUSTEDROUTER_BASE_URL",
    "TRUSTEDROUTER_CLASSIFICATION_MODEL",
    "TRUSTEDROUTER_LLM_CONFIG",
    "TRUSTEDROUTER_REASONING_MODEL",
    "TRUSTEDROUTER_TOOLCALL_MODEL",
    "VERTEX_AI_CLASSIFICATION_MODEL",
    "VERTEX_AI_LLM_CONFIG",
    "VERTEX_AI_REASONING_MODEL",
    "VERTEX_AI_TOOLCALL_MODEL",
    "model_config_for",
]
