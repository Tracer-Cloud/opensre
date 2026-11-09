"""Focused LLM provider ordering for setup surfaces."""

from __future__ import annotations

from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE, SUPPORTED_PROVIDERS, ProviderOption

FOCUSED_SETUP_PROVIDER_VALUES: tuple[str, ...] = ("claude-code", "openai", "openrouter")
DEFAULT_SETUP_PROVIDER_VALUE = "openai"
OTHER_PROVIDER_SELECTION = "__other_llm_provider__"


def focused_setup_provider_options() -> tuple[ProviderOption, ...]:
    """Return the providers shown in the first setup picker."""
    return tuple(PROVIDER_BY_VALUE[value] for value in FOCUSED_SETUP_PROVIDER_VALUES)


def other_setup_provider_options() -> tuple[ProviderOption, ...]:
    """Return supported providers hidden behind the ``Other`` setup picker."""
    featured = set(FOCUSED_SETUP_PROVIDER_VALUES)
    return tuple(provider for provider in SUPPORTED_PROVIDERS if provider.value not in featured)


def ordered_setup_provider_options() -> tuple[ProviderOption, ...]:
    """Return all setup providers with focused choices first."""
    return (*focused_setup_provider_options(), *other_setup_provider_options())


def focused_provider_default(provider_value: str | None) -> str:
    """Map a saved provider to the first-menu selection."""
    if provider_value in FOCUSED_SETUP_PROVIDER_VALUES:
        return str(provider_value)
    if provider_value:
        return OTHER_PROVIDER_SELECTION
    return DEFAULT_SETUP_PROVIDER_VALUE


__all__ = [
    "DEFAULT_SETUP_PROVIDER_VALUE",
    "FOCUSED_SETUP_PROVIDER_VALUES",
    "OTHER_PROVIDER_SELECTION",
    "focused_provider_default",
    "focused_setup_provider_options",
    "ordered_setup_provider_options",
    "other_setup_provider_options",
]
