"""Wizard configuration metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import (
    ANTHROPIC_REASONING_MODEL,
    GEMINI_REASONING_MODEL,
    NVIDIA_REASONING_MODEL,
    OPENAI_REASONING_MODEL,
    OPENROUTER_REASONING_MODEL,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class ModelOption:
    """A selectable default model."""

    value: str
    label: str


@dataclass(frozen=True)
class ProviderOption:
    """Wizard metadata for a supported LLM provider."""

    value: str
    label: str
    group: str
    api_key_env: str
    model_env: str
    default_model: str
    models: tuple[ModelOption, ...]
    #: If set, ``sync_provider_env`` also writes this key (same value) for legacy .env files.
    legacy_model_env: str | None = None


ANTHROPIC_MODELS = (
    ModelOption(value=ANTHROPIC_REASONING_MODEL, label="Claude Opus 4"),
    ModelOption(value="claude-sonnet-4-20250514", label="Claude Sonnet 4"),
)

OPENAI_MODELS = (
    ModelOption(value=OPENAI_REASONING_MODEL, label="GPT-4o"),
    ModelOption(value="gpt-5-mini", label="GPT-5 mini"),
    ModelOption(value="gpt-4-turbo", label="GPT-4 Turbo"),
    ModelOption(value="gpt-4", label="GPT-4"),
)

OPENROUTER_MODELS = (
    ModelOption(value=OPENROUTER_REASONING_MODEL, label="Claude Opus 4 (via OpenRouter)"),
    ModelOption(value="openai/gpt-4o", label="GPT-4o (via OpenRouter)"),
    ModelOption(value="google/gemini-2.5-pro", label="Gemini 2.5 Pro (via OpenRouter)"),
    ModelOption(value="meta-llama/llama-4-maverick:free", label="Llama 4 Maverick (free)"),
)

GEMINI_MODELS = (
    ModelOption(value=GEMINI_REASONING_MODEL, label="Gemini 2.5 Pro"),
    ModelOption(value="gemini-2.5-flash", label="Gemini 2.5 Flash"),
    ModelOption(value="gemini-2.0-flash", label="Gemini 2.0 Flash"),
)

NVIDIA_MODELS = (
    ModelOption(value=NVIDIA_REASONING_MODEL, label="Llama 4 Maverick 17B"),
    ModelOption(value="nvidia/llama-3.1-nemotron-ultra-253b-v1", label="Nemotron Ultra 253B"),
    ModelOption(value="qwen/qwen3-235b-a22b", label="Qwen3 235B"),
)

SUPPORTED_PROVIDERS = (
    ProviderOption(
        value="anthropic",
        label="Anthropic",
        group="Hosted providers",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        default_model=ANTHROPIC_REASONING_MODEL,
        models=ANTHROPIC_MODELS,
        legacy_model_env="ANTHROPIC_MODEL",
    ),
    ProviderOption(
        value="openai",
        label="OpenAI",
        group="Hosted providers",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        default_model=OPENAI_REASONING_MODEL,
        models=OPENAI_MODELS,
        legacy_model_env="OPENAI_MODEL",
    ),
    ProviderOption(
        value="openrouter",
        label="OpenRouter",
        group="OpenAI-compatible",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_REASONING_MODEL",
        default_model=OPENROUTER_REASONING_MODEL,
        models=OPENROUTER_MODELS,
        legacy_model_env="OPENROUTER_MODEL",
    ),
    ProviderOption(
        value="gemini",
        label="Google Gemini",
        group="OpenAI-compatible",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_REASONING_MODEL",
        default_model=GEMINI_REASONING_MODEL,
        models=GEMINI_MODELS,
        legacy_model_env="GEMINI_MODEL",
    ),
    ProviderOption(
        value="nvidia",
        label="NVIDIA NIM",
        group="OpenAI-compatible",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_REASONING_MODEL",
        default_model=NVIDIA_REASONING_MODEL,
        models=NVIDIA_MODELS,
        legacy_model_env="NVIDIA_MODEL",
    ),
)

PROVIDER_BY_VALUE = {provider.value: provider for provider in SUPPORTED_PROVIDERS}
