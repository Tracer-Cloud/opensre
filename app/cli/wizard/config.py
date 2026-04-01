"""Wizard configuration metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import OPENAI_REASONING_MODEL, REASONING_MODEL

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


ANTHROPIC_MODELS = (
    ModelOption(value=REASONING_MODEL, label="Claude Opus 4"),
    ModelOption(value="claude-sonnet-4-20250514", label="Claude Sonnet 4"),
)

OPENAI_MODELS = (
    ModelOption(value=OPENAI_REASONING_MODEL, label="GPT-4o"),
    ModelOption(value="gpt-4-turbo", label="GPT-4 Turbo"),
    ModelOption(value="gpt-4", label="GPT-4"),
)

SUPPORTED_PROVIDERS = (
    ProviderOption(
        value="anthropic",
        label="Anthropic",
        group="Hosted providers",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        default_model=REASONING_MODEL,
        models=ANTHROPIC_MODELS,
    ),
    ProviderOption(
        value="openai",
        label="OpenAI",
        group="Hosted providers",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        default_model=OPENAI_REASONING_MODEL,
        models=OPENAI_MODELS,
    ),
)

PROVIDER_BY_VALUE = {provider.value: provider for provider in SUPPORTED_PROVIDERS}
