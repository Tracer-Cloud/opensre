"""Buzz credential resolution for gateway and message delivery.

``private_key``/``relay_url``/``default_channel``/``auth_tag`` reuse the shared
scheduler credential resolver, so integration-store > environment precedence
stays identical across gateway and message-delivery callers.

This module adds channel resolution (explicit override -> store
``default_channel`` -> ``BUZZ_DEFAULT_CHANNEL`` env) and raises a setup-friendly
error when nothing usable is configured — mirrors
:mod:`integrations.rocketchat.credentials`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from config.constants.buzz import BUZZ_DEFAULT_CHANNEL_ENV
from infrastructure.errors import OpenSREError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuzzCredentials:
    """Resolved Buzz delivery target: private key, relay, channel.

    ``private_key`` and ``auth_tag`` are excluded from the generated
    ``__repr__`` so neither leaks into pytest assertion output, tracebacks, or
    structured log capture.
    """

    relay_url: str = "http://localhost:3000"
    private_key: str = field(default="", repr=False)
    auth_tag: str = field(default="", repr=False)
    channel: str = ""
    buzz_path: str = "buzz"

    @property
    def is_configured(self) -> bool:
        return bool(self.private_key)


def _store_default_channel() -> str:
    try:
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("buzz", {})
        config = entry.get("config", {}) if isinstance(entry, dict) else {}
        if not isinstance(config, dict):
            return ""
        return str(config.get("default_channel") or "").strip()
    except (
        ImportError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RuntimeError,
    ) as exc:
        logger.debug(
            "Failed to resolve Buzz default_channel from the store: %s",
            exc,
            exc_info=True,
        )
        return ""


def _resolve_channel(channel_override: str | None) -> str:
    stripped_override = channel_override.strip() if channel_override else ""
    if stripped_override:
        return stripped_override
    store_channel = _store_default_channel()
    if store_channel:
        return store_channel
    return os.getenv(BUZZ_DEFAULT_CHANNEL_ENV, "").strip()


def load_credentials_from_env(
    *,
    channel_override: str | None = None,
) -> BuzzCredentials:
    """Resolve Buzz credentials for message delivery.

    Raises :class:`OpenSREError` with a setup-friendly suggestion when either
    the private key or a channel is missing.
    """
    from infrastructure.scheduling.scheduler.credentials import resolve_buzz_credentials

    creds = resolve_buzz_credentials({})
    private_key = creds.get("private_key", "")
    relay_url = creds.get("relay_url", "") or "http://localhost:3000"
    auth_tag = creds.get("auth_tag", "")
    buzz_path = creds.get("buzz_path", "") or "buzz"
    channel = _resolve_channel(channel_override)

    if not private_key:
        raise OpenSREError(
            "Buzz is not configured.",
            suggestion=(
                "Configure Buzz with `opensre integrations setup buzz` (or "
                "`opensre onboard`), or export BUZZ_PRIVATE_KEY and BUZZ_RELAY_URL."
            ),
        )

    if not channel:
        raise OpenSREError(
            "Buzz has no channel to deliver to.",
            suggestion=(
                "Pass --chat-id, set a default channel during "
                "`opensre integrations setup buzz`, or export "
                "BUZZ_DEFAULT_CHANNEL=<channel-uuid>."
            ),
        )

    return BuzzCredentials(
        relay_url=relay_url,
        private_key=private_key,
        auth_tag=auth_tag,
        channel=channel,
        buzz_path=buzz_path,
    )
