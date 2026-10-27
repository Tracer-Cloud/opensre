"""Installed Ollama catalog and explicit model validation."""

from __future__ import annotations

from config.llm_credentials import resolve_env_credential
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.model_results import ModelSelectionError
from surfaces.shared.llm_setup.ollama import (
    OllamaModelDiscoveryError,
    list_available_models,
    model_is_available,
)


def discover_models(provider: ProviderOption) -> tuple[tuple[str, str], ...]:
    host = resolve_env_credential(provider.api_key_env, default=provider.credential_default)
    try:
        models = list_available_models(host)
    except OllamaModelDiscoveryError as exc:
        raise ModelSelectionError(f"{exc} Start Ollama with ollama serve, then retry.") from exc
    if not models:
        raise ModelSelectionError(
            "No Ollama models are available. Run ollama pull <model>, then retry."
        )
    return tuple((model, model) for model in models)


def validate_model(provider: ProviderOption, model: str) -> None:
    models = tuple(value for value, _ in discover_models(provider))
    if not model_is_available(model, models):
        raise ModelSelectionError(
            f"Ollama model is not available: {model}. Run ollama pull {model}, then retry."
        )
