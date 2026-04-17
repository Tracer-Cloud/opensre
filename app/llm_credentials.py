"""Secure local storage helpers for LLM API keys."""

from __future__ import annotations

import os
from typing import Final

import keyring  # type: ignore[import-not-found,import-untyped]
import keyring.errors  # type: ignore[import-not-found,import-untyped]

_KEYRING_SERVICE: Final = "opensre.llm"
_DISABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def _keyring_is_disabled() -> bool:
    return os.getenv("OPENSRE_DISABLE_KEYRING", "").strip().lower() in _DISABLED_VALUES


def resolve_llm_api_key(env_var: str) -> str:
    """Resolve an LLM API key from env first, then the local keychain."""
    env_value = os.getenv(env_var, "").strip()
    if env_value:
        return env_value
    if _keyring_is_disabled():
        return ""
    try:
        return (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip()
    except keyring.errors.KeyringError:
        return ""


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
    return bool(resolve_llm_api_key(env_var))


def save_llm_api_key(env_var: str, value: str) -> None:
    """Persist an LLM API key in the user's system keychain."""
    normalized = value.strip()
    if not normalized:
        delete_llm_api_key(env_var)
        return
    if _keyring_is_disabled():
        raise RuntimeError(
            f"Secure local credential storage is disabled. Set {env_var} in your shell instead."
        )
    try:
        keyring.set_password(_KEYRING_SERVICE, env_var, normalized)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            "Secure local credential storage is unavailable. "
            f"Set {env_var} in your shell or configure a working system keychain."
        ) from exc


def delete_llm_api_key(env_var: str) -> None:
    """Remove an LLM API key from the user's system keychain if present."""
    if _keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, env_var)
    except keyring.errors.KeyringError:
        return
