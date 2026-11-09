"""Lazy loaders for verified integrations and LLM settings (repl slash commands)."""

from __future__ import annotations

from typing import Any


def load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from integrations.verify import verify_integrations

    return verify_integrations()


def configured_integration_names() -> list[str]:
    """Return configured integration service names without running verifiers."""
    from integrations.verify import resolve_effective_integrations

    return sorted(resolve_effective_integrations())


def verify_integration(service: str) -> dict[str, str] | None:
    """Verify a single integration and return its result row."""
    from integrations.verify import verify_integrations

    normalized = service.strip().lower()
    if not normalized:
        return None
    rows = verify_integrations(normalized)
    return rows[0] if rows else None


def load_llm_settings() -> Any | None:
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

        return "OpenSRE webapp" if account_llm_route() is not None else "local configuration"
    except Exception:
        return "local configuration"


__all__ = [
    "configured_integration_names",
    "load_llm_source",
    "load_llm_settings",
    "load_verified_integrations",
    "verify_integration",
]
