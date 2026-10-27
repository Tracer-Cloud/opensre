"""Effective model settings and structured source presentation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.llm_settings import LLMSettings


def load_llm_settings() -> LLMSettings | None:
    """Load the effective account-hosted or local LLM settings for display."""
    try:
        from config.account import account_llm_route
        from config.llm_settings import LLMSettings, resolve_llm_settings

        if (route := account_llm_route()) is not None:
            settings = resolve_llm_settings(provider_override="openai")
            return settings.model_copy(
                update={
                    "openai_reasoning_model": route.model,
                    "openai_classification_model": route.model,
                    "openai_toolcall_model": route.model,
                }
            )
        return LLMSettings.from_env()
    except Exception:
        return None


def load_llm_source() -> str:
    """Return the user-facing origin of the effective LLM route."""
    try:
        from config.account import account_llm_route

        if account_llm_route() is not None:
            return "OpenSRE webapp · Hosted credits"
        from surfaces.shared.llm_setup.provider_sources import source_label
        from surfaces.shared.llm_setup.selection import resolve_provider

        return source_label(resolve_provider()) + " · local configuration"
    except Exception:
        return "local configuration"
