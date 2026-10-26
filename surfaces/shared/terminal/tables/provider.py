"""LLM provider and model detection for the interactive shell.

Exported
--------
resolve_provider_models(settings, provider)  -> (reasoning_model, toolcall_model)
detect_provider_model()                      -> (provider, model)
"""

from __future__ import annotations

import os

from config.constants.llm import LLM_PROVIDER_ENV
from core.llm.provider_models import resolve_provider_models


def detect_provider_model() -> tuple[str, str]:
    """Return (provider, model) for the active LLM config."""
    try:
        from config.account import account_llm_route
        from config.llm_settings import LLMSettings

        if (route := account_llm_route()) is not None:
            return ("openai", route.model)
        settings = LLMSettings.from_env()
    except Exception:
        return ("unknown", "unknown")

    provider = settings.provider or os.getenv(LLM_PROVIDER_ENV, "anthropic")
    reasoning_model, _toolcall_model = resolve_provider_models(settings, provider)
    return (provider, reasoning_model)


__all__ = [
    "detect_provider_model",
    "resolve_provider_models",
]
