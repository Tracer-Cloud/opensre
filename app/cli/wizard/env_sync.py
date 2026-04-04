"""Helpers to sync wizard choices into the project .env file."""

from __future__ import annotations

import re
from pathlib import Path

from app.cli.wizard.config import (
    PROJECT_ENV_PATH,
    SUPPORTED_PROVIDERS,
    ProviderOption,
)

_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_LLM_API_KEY_ENVS = tuple(dict.fromkeys(provider.api_key_env for provider in SUPPORTED_PROVIDERS))


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


def _remove_env_value(lines: list[str], key: str) -> list[str]:
    updated: list[str] = []
    for line in lines:
        match = _ENV_ASSIGNMENT.match(line)
        if match and match.group(1) == key:
            continue
        updated.append(line)
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


def sync_provider_env(
    *,
    provider: ProviderOption,
    model: str,
    env_path: Path | None = None,
) -> Path:
    """Write non-secret provider settings into the project .env."""
    target_path = env_path or PROJECT_ENV_PATH
    existing = target_path.read_text(encoding="utf-8").splitlines(keepends=True) if target_path.exists() else []

    values: dict[str, str] = {"LLM_PROVIDER": provider.value, provider.model_env: model}
    if provider.legacy_model_env:
        values[provider.legacy_model_env] = model

    lines = existing
    for key in _LLM_API_KEY_ENVS:
        lines = _remove_env_value(lines, key)
    for key, value in values.items():
        lines = _set_env_value(lines, key, value)

    target_path.write_text("".join(lines), encoding="utf-8")
    return target_path
