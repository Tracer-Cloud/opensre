"""Preflight: can the configured LLM route serve a call, before a turn spends work on it?

Mirrors the routing decision in :func:`core.llm.factory.resolve_llm_route` — the
signed-in account route wins over ``LLM_PROVIDER`` — without building a client.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LLMReadiness:
    """Whether the next LLM call has a configured route, and why not."""

    provider: str
    #: Provider-style failure text when not ready, classified and remediated like
    #: a provider exception by :mod:`core.llm_invoke_errors`; empty when ready.
    reason: str = ""

    @property
    def ready(self) -> bool:
        return not self.reason


def llm_ready() -> LLMReadiness:
    """Report whether the configured route can serve an LLM call.

    The signed-in account route is always ready. Otherwise the configured
    provider's settings must validate and, for an API-key provider, its key must
    be configured. CLI, ambient and local providers prove their auth only at
    request time — a prompt-safe probe cannot always tell (an unprobed CLI login
    reads as unknown) — so only their settings are checked.
    """
    from pydantic import ValidationError

    from config.account import account_llm_route
    from config.llm_auth.provider_catalog import KEYLESS_PROVIDER_VALUES
    from config.llm_settings import (
        PROVIDER_OPENAI,
        get_configured_llm_provider,
        has_credentials_for_active_llm_provider,
        llm_settings_error_message,
        resolve_llm_settings,
    )

    if account_llm_route() is not None:
        return LLMReadiness(provider=PROVIDER_OPENAI)
    provider = get_configured_llm_provider()
    try:
        if provider in KEYLESS_PROVIDER_VALUES:
            resolve_llm_settings()
            return LLMReadiness(provider=provider)
        if has_credentials_for_active_llm_provider():
            return LLMReadiness(provider=provider)
    except ValidationError as exc:
        return LLMReadiness(provider=provider, reason=llm_settings_error_message(exc))
    return LLMReadiness(
        provider=provider, reason=f"Missing credentials for LLM provider '{provider}'."
    )


__all__ = ["LLMReadiness", "llm_ready"]
