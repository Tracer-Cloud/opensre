"""Helpers to sync wizard choices into the project .env file."""

from __future__ import annotations

import re
from pathlib import Path

from app.cli.wizard.config import PROJECT_ENV_PATH, ProviderOption

_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    updated: list[str] = []
    replaced = False
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if not match or match.group(1) != key:
            updated.append(line)
            continue
        if not replaced:
            updated.append(f"{key}={value}\n")
            replaced = True

    if not replaced:
        updated.append(f"{key}={value}\n")
    return updated



def sync_env_values(
    values: dict[str, str],
    *,
    env_path: Path | None = None,
) -> Path:
    """Write multiple environment values into the target .env file."""
    target_path = env_path or PROJECT_ENV_PATH
    existing = target_path.read_text(encoding="utf-8").splitlines(keepends=True) if target_path.exists() else []

    lines = existing
    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    target_path.write_text("".join(lines), encoding="utf-8")
    return target_path


def _provider_specific_keys(p: ProviderOption) -> set[str]:
    """Return all env keys owned by a provider (api key + model keys)."""
    keys = {p.api_key_env, p.model_env}
    if p.legacy_model_env:
        keys.add(p.legacy_model_env)
    return keys


def _remove_keys(lines: list[str], keys_to_remove: set[str]) -> list[str]:
    """Drop lines whose env key is in *keys_to_remove*."""
    result: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) in keys_to_remove:
            continue
        result.append(line)
    return result


def sync_provider_env(
    *,
    provider: ProviderOption,
    model: str,
    env_path: Path | None = None,
) -> Path:
    """Write non-secret provider settings into the project .env.

    Removes stale keys from other providers and all API-key entries
    (API keys are persisted separately via the system keyring).
    """
    from app.cli.wizard.config import SUPPORTED_PROVIDERS

    target_path = env_path or PROJECT_ENV_PATH
    existing = target_path.read_text(encoding="utf-8").splitlines(keepends=True) if target_path.exists() else []

    stale_keys: set[str] = set()
    for p in SUPPORTED_PROVIDERS:
        stale_keys |= _provider_specific_keys(p)

    active_model_keys = {provider.model_env}
    if provider.legacy_model_env:
        active_model_keys.add(provider.legacy_model_env)
    stale_keys -= active_model_keys

    lines = _remove_keys(existing, stale_keys)

    values: dict[str, str] = {"LLM_PROVIDER": provider.value, provider.model_env: model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = model

    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    target_path.write_text("".join(lines), encoding="utf-8")
    return target_path
