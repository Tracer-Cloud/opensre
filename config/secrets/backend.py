"""Shared contracts for the secret storage tiers.

Leaf module: :mod:`config.secrets.local_file` and :mod:`config.secrets.store`
both import it, so the shared error type lives here to keep the package
acyclic.
"""

from __future__ import annotations

from enum import StrEnum


class SecretTier(StrEnum):
    """Which storage tier a resolved secret came from.

    ``fallback`` is the owner-only local credentials file
    (``~/.opensre/credentials.json``); ``none`` means no tier held the secret.
    """

    ENV = "env"
    FALLBACK = "fallback"
    NONE = "none"


class KeyringUnavailableReason(StrEnum):
    """Why local credential storage could not serve a request."""

    DISABLED = "disabled"
    NO_BACKEND = "no_backend"
    BACKEND_ERROR = "backend_error"


class KeyringUnavailableError(RuntimeError):
    """Raised when local credential storage cannot store or return a secret.

    Subclasses ``RuntimeError`` because callers predating the secret-store split
    (``_persist_env_secret``, ``persist_api_key_secret``) already treat a
    ``RuntimeError`` from this layer as "storage refused the write".
    """

    def __init__(self, message: str, *, reason: KeyringUnavailableReason) -> None:
        super().__init__(message)
        self.reason = reason


__all__ = [
    "KeyringUnavailableError",
    "KeyringUnavailableReason",
    "SecretTier",
]
