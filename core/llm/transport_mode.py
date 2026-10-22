"""Global LLM transport mode: native vendor SDKs vs LiteLLM."""

from __future__ import annotations

import os
from typing import Final

# Set to "litellm" to route all API providers through LiteLLM.
# Set to "sdk" (or leave unset) to use native vendor SDKs.
# CLI-backed providers (codex, claude-code) are always routed through their
# subprocess path regardless of this setting.
LLM_TRANSPORT_ENV: Final = "OPENSRE_LLM_TRANSPORT"


def use_litellm_transport() -> bool:
    """Return ``True`` when all API providers should be routed through LiteLLM."""
    return os.getenv(LLM_TRANSPORT_ENV, "").strip().lower() == "litellm"


def use_litellm_for_provider(provider: str) -> bool:
    """Return ``True`` when *provider* must route through LiteLLM."""
    from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
    from core.llm.providers.azure_openai import is_azure_openai_provider
    from core.llm.providers.vertex_ai import is_vertex_ai_provider

    return (
        use_litellm_transport()
        or provider in SUBSCRIPTION_BY_VALUE
        or is_azure_openai_provider(provider)
        or is_vertex_ai_provider(provider)
    )


def current_llm_transport() -> str:
    """Normalized ``OPENSRE_LLM_TRANSPORT`` value (empty when unset)."""
    return os.getenv(LLM_TRANSPORT_ENV, "").strip().lower()
