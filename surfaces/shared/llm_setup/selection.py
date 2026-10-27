"""Validate and commit explicit model selections without terminal interaction."""

from __future__ import annotations

import os

from config.constants.llm import LLM_PROVIDER_ENV
from config.llm_auth import credentials
from core.llm import factory
from surfaces.shared.llm_setup import env_sync
from surfaces.shared.llm_setup.auth_results import SelectionError, SelectionResult
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE, ProviderOption
from surfaces.shared.llm_setup.handoff import notify_setup


def resolve_provider(name: str) -> ProviderOption:
    """Resolve an exact provider identifier or report the valid choices."""
    provider = PROVIDER_BY_VALUE.get(name.strip().lower())
    if provider is None:
        raise SelectionError(
            f"unknown LLM provider: {name}. Choose: {', '.join(PROVIDER_BY_VALUE)}"
        )
    return provider


def normalize_model(model: str) -> str:
    """Normalize an explicitly entered model slug, preserving exact punctuation."""
    return "-".join(model.split())


def model_allowed(provider: ProviderOption, model: str) -> bool:
    """Respect curated choices and the provider's custom-model contract."""
    return model in {option.value for option in provider.models} or (
        bool(model) and provider.allow_custom_models
    )


def default_model(provider: ProviderOption) -> str:
    """Keep configured gateway models when the provider has no built-in default."""
    if provider.default_model or not provider.endpoint_env:
        return provider.default_model
    return os.getenv(provider.model_env, "").strip() or (
        os.getenv(provider.legacy_model_env, "").strip() if provider.legacy_model_env else ""
    )


def validate_model(provider: ProviderOption, model: str) -> None:
    """Reject invalid model selections before changing configuration."""
    if not model and provider.credential_kind != "cli":
        raise SelectionError(f"missing model for {provider.value}")
    if model and not model_allowed(provider, model):
        known = ", ".join(option.value for option in provider.models if option.value)
        raise SelectionError(f"unknown model for {provider.value}: {model}. Known models: {known}")
    from surfaces.shared.llm_setup.model_discovery import validate_available_model

    validate_available_model(provider, model)


def select_provider(
    name: str, model: str | None = None, *, toolcall_model: str | None = None
) -> SelectionResult:
    """Commit an authenticated provider/model together, leaving other connections intact."""
    from config.constants.llm import HOSTED_MODEL_PROVIDER

    if name == HOSTED_MODEL_PROVIDER:
        from surfaces.shared.llm_setup.hosted_connection import select_hosted

        return select_hosted(model)
    provider = resolve_provider(name)
    selected = normalize_model(model) if model is not None else default_model(provider)
    auth = credentials.connection_status(provider.value)
    if provider.api_key_env and provider.credential_secret and not auth.configured:
        raise SelectionError(
            f"missing credential for {provider.value}: {provider.api_key_env} is not set. "
            f"Run opensre auth login {provider.value}."
        )
    if provider.credential_kind == "subscription" and not auth.configured:
        raise SelectionError(
            f"sign in before selecting this subscription: /auth login {provider.value}"
        )
    if provider.endpoint_env and not os.getenv(provider.endpoint_env, "").strip():
        raise SelectionError(
            f"missing base URL for {provider.value}: set {provider.endpoint_env} or run opensre onboard"
        )
    if provider.credential_kind == "cli" and (not auth.configured or auth.stale):
        raise SelectionError(f"Run /login {provider.value} to connect before selecting its models.")
    validate_model(provider, selected)
    selected_toolcall = None
    if toolcall_model is not None and provider.toolcall_model_env:
        selected_toolcall = normalize_model(toolcall_model)
        validate_model(provider, selected_toolcall)
    path = env_sync.sync_provider_env(
        provider=provider, model=selected, toolcall_model=selected_toolcall
    )
    factory.reset_llm_clients()
    notify_setup(selected=True)
    return SelectionResult(provider.value, selected, path, selected_toolcall)


def select_reasoning_model(model: str, *, provider_name: str | None = None) -> SelectionResult:
    """Change the current provider's model without changing its connection source."""
    from config.account import account_llm_route

    if account_llm_route() is not None:
        raise SelectionError(
            "Hosted models are managed by OpenSRE. Select a connected provider instead."
        )
    provider = resolve_provider(provider_name or os.getenv(LLM_PROVIDER_ENV) or "anthropic")
    selected = normalize_model(model)
    validate_model(provider, selected)
    path = env_sync.sync_reasoning_model_env(provider=provider, model=selected)
    factory.reset_llm_clients()
    notify_setup(selected=True)
    return SelectionResult(provider.value, selected, path)


def select_toolcall_model(model: str, *, provider_name: str | None = None) -> SelectionResult:
    """Change the tool-call slot for a provider supporting that slot."""
    from config.account import account_llm_route
    from config.env_file import sync_env_values

    if account_llm_route() is not None:
        raise SelectionError(
            "Hosted models are managed by OpenSRE. Select a connected provider instead."
        )
    provider = resolve_provider(provider_name or os.getenv(LLM_PROVIDER_ENV) or "anthropic")
    if not provider.toolcall_model_env:
        raise SelectionError(f"provider {provider.value} does not expose a separate toolcall model")
    selected = normalize_model(model)
    if not selected:
        raise SelectionError("toolcall model cannot be empty")
    path = sync_env_values({provider.toolcall_model_env: selected})
    os.environ[provider.toolcall_model_env] = selected
    factory.reset_llm_clients()
    notify_setup(selected=True)
    return SelectionResult(provider.value, selected, path)
