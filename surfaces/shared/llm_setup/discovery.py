"""Thin dispatch to existing provider discovery implementations."""

from __future__ import annotations

from collections.abc import Callable

from surfaces.shared.llm_setup.azure_models import discover_models as azure_models
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.ollama_models import discover_models as ollama_models
from surfaces.shared.llm_setup.ollama_models import validate_model as validate_ollama_model

_DISCOVERY: dict[str, Callable[[ProviderOption], tuple[tuple[str, str], ...]]] = {
    "ollama": ollama_models,
    "azure-openai": azure_models,
}
_VALIDATORS: dict[str, Callable[[ProviderOption, str], None]] = {"ollama": validate_ollama_model}


def discover_models(provider: ProviderOption) -> tuple[tuple[str, str], ...] | None:
    loader = _DISCOVERY.get(provider.value)
    return loader(provider) if loader else None


def validate_discovered_model(provider: ProviderOption, model: str) -> None:
    validator = _VALIDATORS.get(provider.value)
    if validator:
        validator(provider, model)


def uses_discovery(provider: ProviderOption) -> bool:
    return provider.value in _DISCOVERY
