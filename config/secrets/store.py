"""The one place that decides where an OpenSRE secret is read from and written to.

Every surface that persists or resolves a credential goes through here, so the
tier order is stated once instead of being re-derived at each call site:

    read   env  ->  owner-only local file
    write  owner-only local file
    delete owner-only local file

The OS keychain is never used: secrets live in ``~/.opensre/credentials.json``,
and ``OPENSRE_DISABLE_KEYRING`` keeps working as the switch that turns local
persistence off entirely (env vars become the only source).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from filelock import Timeout as FileLockTimeout

from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.secrets import local_file
from config.secrets.backend import (
    KeyringUnavailableError,
    KeyringUnavailableReason,
    SecretTier,
)

_DISABLED_VALUES = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class SecretLookup:
    """Where a secret resolved from."""

    value: str
    tier: SecretTier


@dataclass(frozen=True)
class SecretSaveResult:
    """Which tier accepted the write."""

    tier: SecretTier
    detail: str = ""

    @property
    def used_fallback(self) -> bool:
        return self.tier == SecretTier.FALLBACK


def keyring_is_disabled() -> bool:
    """Whether ``OPENSRE_DISABLE_KEYRING`` takes this machine out of local storage.

    Env vars stay the only source when set; nothing is written to disk.
    """
    return os.getenv(OPENSRE_DISABLE_KEYRING_ENV, "").strip().lower() in _DISABLED_VALUES


def lookup(env_var: str, *, default: str = "") -> SecretLookup:
    """Resolve a secret from the environment, then the local file."""
    env_value = os.getenv(env_var, default).strip()
    if env_value:
        return SecretLookup(env_value, SecretTier.ENV)

    # The disable switch takes the machine out of local persistence entirely,
    # so the file is not consulted and env vars are the only source.
    if keyring_is_disabled():
        return SecretLookup("", SecretTier.NONE)

    try:
        stored_value = local_file.get(env_var)
    except local_file.LOCAL_STORE_ERRORS:
        # Contended credential file — miss this call; do not abort startup.
        return SecretLookup("", SecretTier.NONE)
    if stored_value:
        return SecretLookup(stored_value, SecretTier.FALLBACK)
    return SecretLookup("", SecretTier.NONE)


def resolve_secret(env_var: str, *, default: str = "") -> str:
    """Resolve a secret, or ``""`` when no tier has it."""
    return lookup(env_var, default=default).value


def resolve_stored_secret(env_var: str) -> str:
    """Return the file-stored secret, ignoring any environment override."""
    if keyring_is_disabled():
        return ""
    try:
        return local_file.get(env_var)
    except local_file.LOCAL_STORE_ERRORS:
        return ""


def secret_source(env_var: str) -> SecretTier:
    """Which tier would serve this secret, without exposing its value."""
    return lookup(env_var).tier


def save_secret(env_var: str, value: str) -> SecretSaveResult:
    """Persist a secret to the owner-only local file.

    Raises :class:`KeyringUnavailableError` when the write did not land, so a
    caller that sees no exception knows the credential is durable.
    """
    normalized = value.strip()
    if not normalized:
        delete_secret(env_var)
        return SecretSaveResult(SecretTier.NONE, f"{env_var} cleared.")

    if keyring_is_disabled():
        raise KeyringUnavailableError(
            f"{env_var} not saved: local credential storage is disabled. "
            "Export the secret in the process environment instead.",
            reason=KeyringUnavailableReason.DISABLED,
        )
    try:
        local_file.set(env_var, normalized)
    except local_file.LOCAL_STORE_ERRORS as file_exc:
        raise KeyringUnavailableError(
            f"Writing {env_var} to {local_file.store_path()} failed.",
            reason=KeyringUnavailableReason.NO_BACKEND,
        ) from file_exc
    return SecretSaveResult(
        SecretTier.FALLBACK,
        f"{env_var} stored in {local_file.store_path()}.",
    )


def delete_secret(env_var: str) -> None:
    """Remove a stored secret from the local file.

    Absent entries are fine. Failure to clear raises
    :class:`KeyringUnavailableError` — logout must not report success while a
    copy remains resolvable (local file lock timeout).
    """
    try:
        local_file.delete(env_var)
    except (local_file.LocalStoreError, FileLockTimeout, OSError) as exc:
        # Do not suppress: a contended store would leave the credential
        # resolvable after a "successful" logout.
        raise KeyringUnavailableError(
            f"Could not remove the local copy of {env_var}. "
            "Retry logout when the credential store is available.",
            reason=KeyringUnavailableReason.BACKEND_ERROR,
        ) from exc


__all__ = [
    "SecretLookup",
    "SecretSaveResult",
    "delete_secret",
    "keyring_is_disabled",
    "lookup",
    "resolve_secret",
    "resolve_stored_secret",
    "save_secret",
    "secret_source",
]
