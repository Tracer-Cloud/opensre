"""Classify env-var *names* as secret vs public — no I/O, no secret-store imports.

Shared by ``.env`` writers and the one-time keychain importer. Lives in a leaf
module so those call sites cannot form an import cycle through
:mod:`config.env_file` ↔ :mod:`config.secrets`.
"""

from __future__ import annotations

# Names whose terminal token would otherwise flag them sensitive, but whose
# value is meant to be public: Discord's public key verifies signatures rather
# than authenticating, and MongoDB Atlas's public key is a paired identifier
# next to a private key, not a secret on its own.
_NON_SECRET_ENV_KEYS: frozenset[str] = frozenset({"DISCORD_PUBLIC_KEY", "MONGODB_ATLAS_PUBLIC_KEY"})
# Underscore-separated terminal tokens that mark an env var as sensitive.
# Matching the terminal component (rather than a substring or a fixed suffix
# like ``_token``) catches both ``GITLAB_ACCESS_TOKEN`` and a bare ``TOKEN``
# while leaving ``OPENAI_TOKEN_LIMIT`` (terminal ``limit``) alone.
_SENSITIVE_TERMINAL_TOKENS: frozenset[str] = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "key",
        "apikey",
        "credential",
        "credentials",
    }
)
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "connection_string",
    # Inline kubeconfig YAML embeds bearer tokens / client keys / certs; the
    # path-only ``KUBECONFIG`` env var does not match this needle.
    "kubeconfig_content",
)


def is_sensitive_env_key(key: str) -> bool:
    """True when an env var name is a secret (credentials file + ``.env``)."""
    if key in _NON_SECRET_ENV_KEYS:
        return False
    lowered = key.lower()
    terminal = lowered.rsplit("_", 1)[-1]
    if terminal in _SENSITIVE_TERMINAL_TOKENS:
        return True
    return any(needle in lowered for needle in _SENSITIVE_SUBSTRINGS)


__all__ = ["is_sensitive_env_key"]
