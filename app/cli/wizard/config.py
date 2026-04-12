"""Wizard configuration metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    ANTHROPIC_REASONING_MODEL,
    DEFAULT_OLLAMA_MODEL,
    GEMINI_REASONING_MODEL,
    NVIDIA_REASONING_MODEL,
    OPENAI_REASONING_MODEL,
    OPENROUTER_REASONING_MODEL,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_ENV_PATH = Path(os.getenv("OPENSRE_PROJECT_ENV_PATH", PROJECT_ROOT / ".env"))


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
    ModelOption(value=OPENAI_REASONING_MODEL, label="GPT-5.4"),
    ModelOption(value="gpt-5.4-mini", label="GPT-5.4 mini"),
    ModelOption(value="gpt-5.4-nano", label="GPT-5.4 nano"),
    ModelOption(value="gpt-5.3-codex", label="GPT-5.3-Codex"),
)

OPENROUTER_MODELS = (
    ModelOption(value=OPENROUTER_REASONING_MODEL, label="OpenRouter Auto (smart routing)"),
    ModelOption(value="openai/gpt-5.2", label="GPT-5.2 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-opus-4.6", label="Claude Opus 4.6 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-sonnet-4.5", label="Claude Sonnet 4.5 (via OpenRouter)"),
    ModelOption(value="anthropic/claude-haiku-4.5", label="Claude Haiku 4.5 (via OpenRouter)"),
    ModelOption(
        value="google/gemini-3.1-pro-preview", label="Gemini 3.1 Pro (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3-flash-preview", label="Gemini 3 Flash (preview, via OpenRouter)"
    ),
    ModelOption(
        value="google/gemini-3.1-flash-lite-preview",
        label="Gemini 3.1 Flash-Lite (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3.1-flash-image-preview",
        label="Gemini 3.1 Flash Image (preview, via OpenRouter)",
    ),
    ModelOption(
        value="google/gemini-3-pro-image-preview",
        label="Gemini 3 Pro Image (preview, via OpenRouter)",
    ),
    ModelOption(value="meta-llama/llama-4-maverick", label="Llama 4 Maverick (via OpenRouter)"),
    ModelOption(value="meta-llama/llama-4-scout", label="Llama 4 Scout (via OpenRouter)"),
    ModelOption(value="mistralai/mistral-large-2512", label="Mistral Large 3 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4", label="Grok 4 (via OpenRouter)"),
    ModelOption(value="x-ai/grok-4-fast", label="Grok 4 Fast (via OpenRouter)"),
    ModelOption(value="moonshotai/kimi-k2.5", label="Kimi K2.5 (via OpenRouter)"),
    ModelOption(value="z-ai/glm-4.7", label="GLM 4.7 (via OpenRouter)"),
    ModelOption(value="minimax/minimax-m2", label="MiniMax M2 (via OpenRouter)"),
    ModelOption(value="deepseek/deepseek-v3.2", label="DeepSeek V3.2 (via OpenRouter)"),
    ModelOption(value="qwen/qwen-3.6-plus-preview", label="Qwen 3.6 Plus (via OpenRouter)"),
)

GEMINI_MODELS = (
    ModelOption(value=GEMINI_REASONING_MODEL, label="Gemini 3.1 Pro (preview)"),
    ModelOption(value="gemini-3-flash-preview", label="Gemini 3 Flash (preview)"),
    ModelOption(value="gemini-3.1-flash-lite-preview", label="Gemini 3.1 Flash-Lite (preview)"),
    ModelOption(value="gemini-3.1-flash-image-preview", label="Gemini 3.1 Flash Image (preview)"),
    ModelOption(value="gemini-3-pro-image-preview", label="Gemini 3 Pro Image (preview)"),
)

NVIDIA_MODELS = (
    ModelOption(
        value=NVIDIA_REASONING_MODEL,
        label="Nemotron 3 Super 120B (5x higher throughput for agentic AI)",
    ),
    ModelOption(value="nvidia/nemotron-3-nano-30b-a3b", label="Nemotron 3 Nano 30B"),
)

OLLAMA_MODELS = (
    ModelOption(value="llama3.2", label="Llama 3.2 (3B) — recommended"),
    ModelOption(value="llama3.1:8b", label="Llama 3.1 (8B)"),
    ModelOption(value="mistral", label="Mistral 7B"),
    ModelOption(value="qwen2.5:7b", label="Qwen 2.5 (7B)"),
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
        group="Hosted providers",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_REASONING_MODEL",
        default_model=OPENROUTER_REASONING_MODEL,
        models=OPENROUTER_MODELS,
        legacy_model_env="OPENROUTER_MODEL",
    ),
    ProviderOption(
        value="gemini",
        label="Google Gemini",
        group="Hosted providers",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_REASONING_MODEL",
        default_model=GEMINI_REASONING_MODEL,
        models=GEMINI_MODELS,
        legacy_model_env="GEMINI_MODEL",
    ),
    ProviderOption(
        value="nvidia",
        label="NVIDIA NIM",
        group="Hosted providers",
        api_key_env="NVIDIA_API_KEY",
        model_env="NVIDIA_REASONING_MODEL",
        default_model=NVIDIA_REASONING_MODEL,
        models=NVIDIA_MODELS,
        legacy_model_env="NVIDIA_MODEL",
    ),
    ProviderOption(
        value="ollama",
        label="Ollama (local)",
        group="Local providers",
        api_key_env="OLLAMA_HOST",
        model_env="OLLAMA_MODEL",
        default_model=DEFAULT_OLLAMA_MODEL,
        models=OLLAMA_MODELS,
    ),
)

PROVIDER_BY_VALUE = {provider.value: provider for provider in SUPPORTED_PROVIDERS}
