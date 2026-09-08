"""Provider-neutral dispatch for model discovery and availability checks."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from surfaces.interactive_shell.command_registry.model.ollama import (
    model_menu_choices as ollama_model_menu_choices,
)
from surfaces.interactive_shell.command_registry.model.ollama import (
    validate_model_available as validate_ollama_model_available,
)
from surfaces.shared.llm_setup.catalog import ProviderOption

ModelChoices = list[tuple[str, str]]
ModelChoicesLoader = Callable[[ProviderOption, Console], ModelChoices | None]
ModelValidator = Callable[[ProviderOption, str, Console], bool]

_CHOICES_BY_PROVIDER: dict[str, ModelChoicesLoader] = {
    "ollama": ollama_model_menu_choices,
}
_VALIDATOR_BY_PROVIDER: dict[str, ModelValidator] = {
    "ollama": validate_ollama_model_available,
}


def model_menu_choices(
    provider: ProviderOption,
    console: Console,
    *,
    fallback: ModelChoices,
) -> ModelChoices | None:
    """Return discovered choices when supported, otherwise ``fallback``."""
    loader = _CHOICES_BY_PROVIDER.get(provider.value)
    return loader(provider, console) if loader is not None else fallback


def validate_model_available(
    provider: ProviderOption,
    model: str,
    console: Console,
) -> bool:
    """Run a provider availability check when one is registered."""
    validator = _VALIDATOR_BY_PROVIDER.get(provider.value)
    return validator(provider, model, console) if validator is not None else True


__all__ = ["model_menu_choices", "validate_model_available"]
