"""Cache-key helpers for LLM singleton invalidation."""

from __future__ import annotations


def current_llm_client_cache_key() -> tuple[str, str]:
    """Return ``(transport, runtime_provider)`` for singleton cache invalidation."""
    from config.account import account_llm_route

    if (route := account_llm_route()) is not None:
        return ("sdk", f"account:openai:{route.base_url}:{route.model}")

    from config.llm_settings import get_configured_llm_provider
    from core.llm.transport_mode import current_llm_transport

    provider = get_configured_llm_provider()
    from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE

    if provider in SUBSCRIPTION_BY_VALUE:
        from core.llm.provider_models import resolve_provider_models

        # Tokens and account selection resolve per request; only model state is cached.
        return ("litellm", f"{provider}:{resolve_provider_models(object(), provider)[0]}")
    return (current_llm_transport(), provider)


__all__ = ["current_llm_client_cache_key"]
