"""Secure local storage helpers for OpenSRE secrets (LLM keys and integrations).

The stable façade over :mod:`config.secrets`: integrations, providers, and the
CLI import from here, so the tier policy can change without touching 30 call
sites. Historically named for LLM API keys; the same storage backs integration
secrets (``TELEGRAM_BOT_TOKEN``, ``ROCKETCHAT_AUTH_TOKEN``, …).
"""

from __future__ import annotations

from config.secrets.guidance import get_keyring_setup_instructions
from config.secrets.records import (
    delete_llm_credential_record,
    resolve_llm_credential_record,
    save_llm_credential_record,
)
from config.secrets.store import (
    SecretSaveResult,
    delete_secret,
    resolve_secret,
    save_secret,
    secret_source,
)

__all__ = [
    "SecretSaveResult",
    "delete_credential",
    "delete_llm_credential_record",
    "get_keyring_setup_instructions",
    "resolve_env_credential",
    "resolve_llm_credential_record",
    "save_credential",
    "save_llm_credential_record",
    "secret_source",
]


def resolve_env_credential(env_var: str, *, default: str = "") -> str:
    """Resolve a credential from process env, then the local credentials file."""
    return resolve_secret(env_var, default=default)


def save_credential(env_var: str, value: str) -> SecretSaveResult:
    """Persist a secret to the owner-only local credentials file.

    Raises ``KeyringUnavailableError`` (a ``RuntimeError``) only when no tier
    accepted the write.
    """
    return save_secret(env_var, value)


def delete_credential(env_var: str) -> None:
    """Remove a secret from every local tier."""
    delete_secret(env_var)
