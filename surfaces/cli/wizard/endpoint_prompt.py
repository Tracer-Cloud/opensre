"""Provider-agnostic onboarding endpoint (base-URL) collection.

Some providers need a user-supplied endpoint before the validation probe can
run: Azure OpenAI (a resource URL) and the custom OpenAI-/Anthropic-compatible
gateways (an arbitrary base URL). The onboarding flow calls
:func:`ensure_endpoint_settings` for every provider right before validating the
credential; providers that need no endpoint return an empty dict. Provider-specific
prompting lives in the owning modules (``azure_openai``/``custom_endpoints``);
this file is a thin dispatcher.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from surfaces.shared.llm_setup.catalog import ProviderOption


def ensure_endpoint_settings(provider: ProviderOption) -> dict[str, str] | None:
    """Return endpoint env vars for *provider*, prompting when missing.

    Returns an empty dict when the provider needs no endpoint, the endpoint env
    mapping when configured/collected, or ``None`` when the user backs out.
    """
    from core.llm.providers.azure_openai import is_azure_openai_provider
    from core.llm.providers.custom_endpoints import is_custom_provider

    if is_azure_openai_provider(provider.value):
        from surfaces.cli.wizard.azure_openai import ensure_endpoint_settings as _azure

        return _azure(provider)
    if is_custom_provider(provider.value):
        from surfaces.cli.wizard.custom_endpoints import ensure_endpoint_settings as _custom

        return _custom(provider)
    return {}
