"""Provider-neutral model discovery and request-free selection checks."""

from __future__ import annotations

import httpx

from surfaces.shared.llm_setup.azure_openai import provider_models as azure_models
from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.ollama import provider_models, require_available_model

_LOADERS = {"ollama": provider_models, "azure-openai": azure_models}
_VALIDATORS = {"ollama": require_available_model}


def discover_models(provider: ProviderOption) -> list[tuple[str, str]] | None:
    """Discover a catalog only when explicitly requested, never during redraw."""
    if provider.credential_kind == "subscription":
        from core.llm.subscriptions.registry import subscription_adapter
        from surfaces.shared.llm_setup.auth_results import SelectionError

        try:
            return subscription_adapter(provider.value).auth.models()
        except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
            raise SelectionError(
                f"Cannot load {provider.label} models. Run /auth verify {provider.value} "
                f"or /login {provider.value} to reconnect. No other source was selected."
            ) from exc
    loader = _LOADERS.get(provider.value)
    return loader(provider) if loader else None


def validate_available_model(provider: ProviderOption, model: str) -> None:
    """Enforce discovered availability where the provider exposes a bounded catalog."""
    from surfaces.shared.llm_setup.auth_results import SelectionError

    validator = _VALIDATORS.get(provider.value)
    if validator is not None:
        validator(provider, model)
        return
    if provider.credential_kind != "subscription":
        return
    models = discover_models(provider)
    if models is None:
        return
    available = tuple(value for value, _label in models)
    if model not in available:
        raise SelectionError(
            f"{provider.label} model is not available: {model}. Available models: {', '.join(available)}"
        )
