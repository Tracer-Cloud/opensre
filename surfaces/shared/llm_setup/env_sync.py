"""Sync LLM-provider choices into the project .env file.

Maps a wizard provider selection onto env keys: which keys the provider owns,
stripping the previous provider's keys on a switch, and mirroring the selection
into the wizard store. File and secret-store I/O uses ``config.env_file``.
"""

from __future__ import annotations

import os
from pathlib import Path

from config.constants.llm import LLM_AUTH_METHOD_ENV, LLM_PROVIDER_ENV, OPENSRE_LLM_CONNECTION_ENV
from config.env_file import (
    PROJECT_ENV_PATH,
    env_assignment_key,
    read_env_lines,
    set_env_value,
    sync_env_values,
    write_env_lines,
)
from config.env_key_sensitivity import is_sensitive_env_key
from surfaces.shared.llm_setup.catalog import ProviderOption, WizardCredentialKind


def sync_reasoning_model_env(
    *,
    provider: ProviderOption,
    model: str,
    env_path: Path | None = None,
) -> Path:
    """Write reasoning model env vars to ``.env``, update runtime env, and sync the setup store."""
    values: dict[str, str] = {provider.model_env: model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = model
    # Resolve the default here rather than letting ``sync_env_values`` fall back
    # to its own: ``sync_provider_env`` below already resolves against this
    # module's ``PROJECT_ENV_PATH``, and both writers must agree on the target.
    target_path = sync_env_values(values, env_path=env_path or PROJECT_ENV_PATH)
    os.environ.update(values)
    _sync_llm_selection_to_store(provider=provider, model=model)
    return target_path


def _sync_llm_selection_to_store(*, provider: ProviderOption, model: str) -> None:
    from config.setup_store import update_local_llm_selection

    update_local_llm_selection(
        provider=provider.value,
        model=model,
        api_key_env=provider.api_key_env or "",
        model_env=provider.model_env,
    )


def _classification_model_env(p: ProviderOption) -> str | None:
    if p.classification_model_env:
        return p.classification_model_env
    if p.model_env.endswith("_REASONING_MODEL"):
        return p.model_env.replace("_REASONING_MODEL", "_CLASSIFICATION_MODEL")
    return None


def _provider_specific_keys(p: ProviderOption) -> set[str]:
    """Return all env keys owned by a provider (api key + model keys)."""
    keys: set[str] = {p.model_env}
    if p.api_key_env:
        keys.add(p.api_key_env)
    if p.legacy_model_env:
        keys.add(p.legacy_model_env)
    if p.toolcall_model_env:
        keys.add(p.toolcall_model_env)
    if p.endpoint_env:
        keys.add(p.endpoint_env)
    if p.api_version_env:
        keys.add(p.api_version_env)
    classification_env = _classification_model_env(p)
    if classification_env:
        keys.add(classification_env)
    return keys


def _env_value_from_lines(lines: list[str], key: str) -> str | None:
    for line in lines:
        if env_assignment_key(line) == key:
            _, _, rhs = line.partition("=")
            return rhs.strip().strip("\"'") or None
    return None


def _remove_keys(lines: list[str], keys_to_remove: set[str]) -> list[str]:
    """Drop lines whose env key is in *keys_to_remove*."""
    result: list[str] = []
    for line in lines:
        key = env_assignment_key(line)
        if key and key in keys_to_remove:
            continue
        result.append(line)
    return result


def reload_provider_selection() -> None:
    """Apply a child login's saved connection without reloading unrelated exports."""
    from config.local_env import get_project_env_path
    from core.llm.transport_mode import LLM_TRANSPORT_ENV
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    lines = read_env_lines(get_project_env_path())
    provider_value = _env_value_from_lines(lines, LLM_PROVIDER_ENV)
    provider = PROVIDER_BY_VALUE.get(provider_value or "")
    if provider is None:
        return
    keys = _provider_specific_keys(provider) | {
        LLM_PROVIDER_ENV,
        OPENSRE_LLM_CONNECTION_ENV,
        LLM_TRANSPORT_ENV,
    }
    for key in keys:
        value = _env_value_from_lines(lines, key)
        if value is not None:
            os.environ[key] = value
        elif key == LLM_TRANSPORT_ENV:
            os.environ.pop(key, None)


def sync_provider_env(
    *,
    provider: ProviderOption,
    model: str,
    toolcall_model: str | None = None,
    extra_env: dict[str, str] | None = None,
    env_path: Path | None = None,
) -> Path:
    """Write provider settings into the project .env.

    Removes stale non-secret keys from other providers. Existing API keys and
    other secrets are left in place.
    """
    from surfaces.shared.llm_setup.catalog import SUPPORTED_PROVIDERS

    target_path = env_path or PROJECT_ENV_PATH
    existing = read_env_lines(target_path)

    # Strip stale model/endpoint keys from other providers. API keys and other
    # secrets stay in ``.env`` — switching providers must not delete them.
    keys_to_remove: set[str] = set()
    for p in SUPPORTED_PROVIDERS:
        keys_to_remove |= _provider_specific_keys(p)

    # Legacy OAuth-era key: no longer written, always scrubbed from old files.
    keys_to_remove.add(LLM_AUTH_METHOD_ENV)
    from core.llm.transport_mode import LLM_TRANSPORT_ENV

    keys_to_remove.add(LLM_TRANSPORT_ENV)

    # Keep the active provider's model keys. Sensitive keys are dropped from
    # the removal set below so a rewrite never deletes an existing API key.
    active_non_secret: set[str] = {provider.model_env}
    if provider.legacy_model_env:
        active_non_secret.add(provider.legacy_model_env)
    if provider.toolcall_model_env:
        active_non_secret.add(provider.toolcall_model_env)
    classification_env = _classification_model_env(provider)
    if classification_env:
        active_non_secret.add(classification_env)
    from core.llm.providers.custom_endpoints import is_custom_provider

    # Providers that carry a user-supplied endpoint (Azure + the custom gateways)
    # keep their base URL in .env; without this it is stripped on the next sync.
    provider_has_endpoint = provider.value == "azure-openai" or is_custom_provider(provider.value)
    if provider_has_endpoint and provider.endpoint_env:
        active_non_secret.add(provider.endpoint_env)
    if provider.value == "azure-openai" and provider.api_version_env:
        active_non_secret.add(provider.api_version_env)
    # A ``host`` credential (e.g. the Ollama host) is non-secret runtime config
    # that the wizard persists to ``.env`` — keep it as an active key so this
    # sync does not strip it back out in the same wizard run.
    if provider.credential_kind == WizardCredentialKind.HOST and provider.api_key_env:
        active_non_secret.add(provider.api_key_env)
    keys_to_remove -= active_non_secret
    keys_to_remove = {key for key in keys_to_remove if not is_sensitive_env_key(key)}

    lines = _remove_keys(existing, keys_to_remove)

    # Custom gateways ship no default model (the user's gateway serves its own
    # names). A switch/restore that supplies no model must NOT blank a
    # previously-working one — mirror the base-URL guard below and preserve the
    # configured model instead of writing an empty CUSTOM_*_MODEL that would fail
    # the required-model validation on the next load. A CLI provider keeps writing
    # an empty model (its CLI default), so scope this strictly to custom gateways.
    resolved_model = model
    if not resolved_model and is_custom_provider(provider.value):
        legacy_env = provider.legacy_model_env
        resolved_model = (
            _env_value_from_lines(lines, provider.model_env)
            or os.getenv(provider.model_env, "").strip()
            or (
                _env_value_from_lines(lines, legacy_env) or os.getenv(legacy_env, "").strip()
                if legacy_env
                else ""
            )
        )

    values: dict[str, str] = {
        "LLM_PROVIDER": provider.value,
        OPENSRE_LLM_CONNECTION_ENV: "local",
        provider.model_env: resolved_model,
    }
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = resolved_model
    if toolcall_model and provider.toolcall_model_env:
        values[provider.toolcall_model_env] = toolcall_model
    if provider.value == "azure-openai":
        # Azure forces the LiteLLM transport; the custom gateways stay on the
        # native SDK, so they must NOT write LLM_TRANSPORT_ENV.
        values[LLM_TRANSPORT_ENV] = "litellm"
        if provider.api_version_env:
            from core.llm.providers.azure_openai import resolve_azure_openai_api_version

            values[provider.api_version_env] = resolve_azure_openai_api_version()
    if provider_has_endpoint and provider.endpoint_env:
        preserved_base = (
            _env_value_from_lines(lines, provider.endpoint_env)
            or os.getenv(provider.endpoint_env, "").strip()
        )
        if preserved_base:
            values[provider.endpoint_env] = preserved_base
    if extra_env:
        values.update(extra_env)

    for key, value in values.items():
        lines = set_env_value(lines, key, value)

    write_env_lines(target_path, lines)

    for key in keys_to_remove:
        os.environ.pop(key, None)
    for key in active_non_secret:
        preserved = _env_value_from_lines(lines, key)
        if preserved is not None:
            values[key] = preserved
    os.environ.update(values)
    _sync_llm_selection_to_store(provider=provider, model=resolved_model)

    return target_path
