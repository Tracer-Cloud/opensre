"""Validated model selection shared by terminal surfaces and tool ports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import config.llm_auth.credentials as credentials
from config.constants.llm import HOSTED_MODEL_SOURCE_ID as HOSTED_SOURCE
from config.constants.llm import LLM_PROVIDER_ENV, OPENSRE_LLM_CONNECTION_ENV
from surfaces.shared.llm_setup.catalog import (
    PROVIDER_BY_VALUE,
    ProviderOption,
    WizardCredentialKind,
)
from surfaces.shared.llm_setup.env_sync import sync_provider_env
from surfaces.shared.llm_setup.model_results import ModelSelectionError
from surfaces.shared.llm_setup.provider_sources import source_label


@dataclass(frozen=True)
class SelectionResult:
    """Non-secret outcome of a committed selection."""

    provider: str
    model: str
    env_path: Path
    changed: bool
    toolcall_model: str | None = None
    source: str = ""


def normalize_model_id(model: str) -> str:
    return "-".join(model.split())


def resolve_provider(name: str | None = None) -> ProviderOption:
    key = (name or os.getenv(LLM_PROVIDER_ENV, "anthropic")).strip().lower()
    provider = PROVIDER_BY_VALUE.get(key)
    if provider is None:
        raise ModelSelectionError(f"unknown LLM provider: {key}. Run opensre model list.")
    return provider


def _resolve_omitted_model(provider: ProviderOption) -> str:
    from config.llm_auth.model_catalog import cached_model_catalog
    from surfaces.shared.llm_setup.discovery import uses_discovery

    if provider.credential_kind == WizardCredentialKind.SUBSCRIPTION or uses_discovery(provider):
        catalog = cached_model_catalog(provider.value)
        if catalog:
            return (
                provider.default_model
                if provider.default_model in {value for value, _ in catalog}
                else catalog[0][0]
            )
        if uses_discovery(provider) and provider.endpoint_env:
            return os.getenv(provider.model_env, "").strip() or (
                os.getenv(provider.legacy_model_env, "").strip()
                if provider.legacy_model_env
                else ""
            )
    if provider.default_model:
        return provider.default_model
    from core.llm.providers.custom_endpoints import is_custom_provider

    if is_custom_provider(provider.value):
        return os.getenv(provider.model_env, "").strip() or (
            os.getenv(provider.legacy_model_env, "").strip() if provider.legacy_model_env else ""
        )
    return ""


def _is_model_allowed(provider: ProviderOption, model: str) -> bool:
    return model in {m.value for m in provider.models} or bool(
        model and provider.allow_custom_models
    )


def _reset_runtime_llm_caches() -> None:
    from core.llm.factory import reset_llm_clients

    reset_llm_clients()


def validate_selection(provider: ProviderOption, model: str) -> None:
    """Check configuration and provider-owned access rules without paid API probes."""
    from surfaces.shared.llm_setup.model_access import validate_model_access

    state = credentials.status(provider.value)
    if provider.credential_kind in (
        WizardCredentialKind.API_KEY,
        WizardCredentialKind.SUBSCRIPTION,
    ) and (not state.configured or state.stale):
        raise ModelSelectionError(
            f"missing credential for {provider.value} ({provider.api_key_env or 'subscription'}). Run opensre auth login {provider.value} "
            f"(/auth login {provider.value} in the shell), then retry."
        )
    if provider.endpoint_env and not os.getenv(provider.endpoint_env, "").strip():
        raise ModelSelectionError(
            f"missing base URL for {provider.value}: set {provider.endpoint_env}, or run opensre auth login {provider.value}."
        )
    if provider.endpoint_env and not model:
        raise ModelSelectionError(f"missing model for {provider.value}: pass an exact model ID.")
    if model and not _is_model_allowed(provider, model):
        raise ModelSelectionError(f"unknown model for {provider.value}: {model}")
    validate_model_access(provider, model)


def select_provider(
    provider_name: str,
    model: str | None = None,
    *,
    toolcall_model: str | None = None,
    env_path: Path | None = None,
) -> SelectionResult:
    """Validate the entire choice before committing active routing and model settings."""
    if provider_name == HOSTED_SOURCE:
        from surfaces.shared.llm_setup.model_inventory import hosted_choice

        choice = hosted_choice()
        if choice is None or toolcall_model is not None:
            raise ModelSelectionError(
                "OpenSRE hosted selection is unavailable or does not support a toolcall override."
            )
        return select_hosted_model(model if model is not None else choice.model_id)
    provider = resolve_provider(provider_name)
    selected = normalize_model_id(model) if model is not None else _resolve_omitted_model(provider)
    validate_selection(provider, selected)
    toolcall = None
    if toolcall_model is not None and provider.toolcall_model_env:
        toolcall = normalize_model_id(toolcall_model)
        if not toolcall or not _is_model_allowed(provider, toolcall):
            raise ModelSelectionError(f"unknown model for {provider.value}: {toolcall}")
        from surfaces.shared.llm_setup.model_access import validate_model_access

        validate_model_access(provider, toolcall)
    changed = (
        os.getenv(LLM_PROVIDER_ENV),
        os.getenv(provider.model_env),
        os.getenv(OPENSRE_LLM_CONNECTION_ENV),
    ) != (provider.value, selected, "local")
    path = sync_provider_env(
        provider=provider, model=selected, toolcall_model=toolcall, env_path=env_path
    )
    _reset_runtime_llm_caches()
    return SelectionResult(
        provider.value, selected, path, changed, toolcall, source_label(provider)
    )


def select_reasoning_model(model: str, *, provider_name: str | None = None) -> SelectionResult:
    provider = resolve_provider(provider_name)
    selected = normalize_model_id(model)
    if not selected:
        raise ModelSelectionError("reasoning model cannot be empty")
    return select_provider(provider.value, selected)


def select_toolcall_model(model: str, *, provider_name: str | None = None) -> SelectionResult:
    from config.env_file import sync_env_values

    provider = resolve_provider(provider_name)
    if not provider.toolcall_model_env:
        raise ModelSelectionError(
            f"provider {provider.value} does not expose a separate toolcall model"
        )
    selected = normalize_model_id(model)
    if not selected:
        raise ModelSelectionError("toolcall model cannot be empty")
    changed = os.getenv(provider.toolcall_model_env) != selected
    values = {provider.toolcall_model_env: selected}
    path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()
    return SelectionResult(
        provider.value, selected, path, changed, selected, source_label(provider)
    )


def restore_provider(provider_name: str) -> SelectionResult:
    return select_provider(provider_name)


def select_hosted_model(model: str) -> SelectionResult:
    """Explicitly return to the signed-in account route without replacing identity."""
    from config.account import load_account_record, resolve_account_token
    from config.constants.account import (
        OPENSRE_ACCOUNT_LLM_MODEL_ENV,
        OPENSRE_GATEWAY_LLM_MODEL_DEFAULT,
    )
    from config.env_file import sync_env_values

    record = load_account_record()
    expected = (
        record.llm_model
        if record
        else os.getenv(OPENSRE_ACCOUNT_LLM_MODEL_ENV) or OPENSRE_GATEWAY_LLM_MODEL_DEFAULT
    )
    if not resolve_account_token() or model != expected:
        raise ModelSelectionError("OpenSRE hosted model is unavailable. Run opensre account login.")
    changed = os.getenv(OPENSRE_LLM_CONNECTION_ENV) == "local"
    values = {OPENSRE_LLM_CONNECTION_ENV: "account"}
    path = sync_env_values(values)
    os.environ.update(values)
    _reset_runtime_llm_caches()
    return SelectionResult(HOSTED_SOURCE, model, path, changed, source="OpenSRE · Hosted credits")
