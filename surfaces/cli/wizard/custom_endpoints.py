"""Onboarding endpoint (base-URL) collection for the custom gateway providers.

Mirrors :mod:`surfaces.cli.wizard.azure_openai`: the provider-agnostic
:func:`surfaces.cli.wizard.endpoint_prompt.ensure_endpoint_settings` dispatcher
delegates here for ``custom-openai`` / ``custom-anthropic`` so all custom-gateway
prompting logic lives in one owning module instead of the shared dispatcher.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from surfaces.cli.wizard.components import Choice
    from surfaces.shared.llm_setup.catalog import ProviderOption

CUSTOM_ENDPOINT_SELECTION = "__custom-api-endpoint__"
_CUSTOM_PROVIDER_VALUES = frozenset(("custom-openai", "custom-anthropic"))


def onboarding_provider_choices(choices: list[Choice]) -> list[Choice]:
    """Collapse both custom protocols into one discoverable onboarding choice."""
    from surfaces.cli.wizard.components import Choice

    custom = Choice(
        value=CUSTOM_ENDPOINT_SELECTION,
        label="Custom API endpoint",
        hint="Use your own OpenAI- or Anthropic-compatible base URL",
    )
    return [custom, *(choice for choice in choices if choice.value not in _CUSTOM_PROVIDER_VALUES)]


def onboarding_provider_default(provider: str | None) -> str | None:
    """Map a saved custom provider to the collapsed onboarding choice."""
    if provider in _CUSTOM_PROVIDER_VALUES:
        return CUSTOM_ENDPOINT_SELECTION
    return provider


def resolve_onboarding_provider(selection: str, *, default: str | None) -> str:
    """Resolve the custom choice through a second protocol dropdown."""
    if selection != CUSTOM_ENDPOINT_SELECTION:
        return selection

    from surfaces.cli.wizard.components import Choice, choose, step

    step("Custom API Endpoint")
    return choose(
        "Choose endpoint compatibility",
        [
            Choice(
                value="custom-openai",
                label="OpenAI-compatible API",
                hint="Chat Completions API (LiteLLM proxy, vLLM, LocalAI, …)",
            ),
            Choice(
                value="custom-anthropic",
                label="Anthropic-compatible API",
                hint="Anthropic Messages API",
            ),
        ],
        default=default if default in _CUSTOM_PROVIDER_VALUES else "custom-openai",
        back_on_cancel=True,
    )


def _base_url_normalizer(provider: ProviderOption) -> Callable[[str], str]:
    """Return the base-URL normalizer for a custom provider.

    custom-anthropic uses the Anthropic-SDK normalizer (strips a trailing /v1,
    since the SDK appends /v1/messages); custom-openai keeps its /v1 verbatim.
    """
    from config.constants.llm import (
        normalize_anthropic_base_url,
        normalize_custom_base_url,
    )
    from core.llm.providers.custom_endpoints import is_custom_anthropic_provider

    if is_custom_anthropic_provider(provider.value):
        return normalize_anthropic_base_url
    return normalize_custom_base_url


def ensure_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return the custom gateway base URL, prompting when it isn't set yet.

    Returns an empty dict when the provider declares no endpoint env, the
    endpoint mapping when configured/collected, or ``None`` when the user backs
    out of the prompt.
    """
    if not provider.endpoint_env:
        return {}
    normalize = _base_url_normalizer(provider)
    configured = normalize(os.getenv(provider.endpoint_env, ""))
    if configured:
        return {provider.endpoint_env: configured}
    return _prompt_endpoint(provider)


def _prompt_endpoint(provider: ProviderOption) -> dict[str, str] | None:
    from infrastructure.terminal.theme import ERROR
    from surfaces.cli.wizard.components import WizardBack, console, prompt_value, step

    normalize = _base_url_normalizer(provider)
    step("Endpoint")
    try:
        raw = prompt_value(
            f"Base URL ({provider.endpoint_env})",
            default=os.getenv(provider.endpoint_env, provider.credential_default),
            secret=False,
            back_on_cancel=True,
        )
    except WizardBack:
        return None
    normalized = normalize(raw)
    if not normalized:
        console.print(f"[{ERROR}]A base URL is required for this provider.[/]")
        return None
    return {provider.endpoint_env: normalized}
