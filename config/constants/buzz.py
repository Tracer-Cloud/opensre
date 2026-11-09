"""Buzz environment variable names.

``BUZZ_PRIVATE_KEY`` is the agent's Nostr identity and is routed to the
keyring by ``config.env_key_sensitivity.is_sensitive_env_key`` (terminal
token ``key``); the rest mirror to ``.env``.
"""

from __future__ import annotations

BUZZ_PATH_ENV = "BUZZ_PATH"
BUZZ_PRIVATE_KEY_ENV = "BUZZ_PRIVATE_KEY"
BUZZ_RELAY_URL_ENV = "BUZZ_RELAY_URL"
BUZZ_DEFAULT_CHANNEL_ENV = "BUZZ_DEFAULT_CHANNEL"
BUZZ_AUTH_TAG_ENV = "BUZZ_AUTH_TAG"

__all__ = [
    "BUZZ_AUTH_TAG_ENV",
    "BUZZ_DEFAULT_CHANNEL_ENV",
    "BUZZ_PATH_ENV",
    "BUZZ_PRIVATE_KEY_ENV",
    "BUZZ_RELAY_URL_ENV",
]
