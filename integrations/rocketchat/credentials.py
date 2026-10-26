"""Rocket.Chat credential resolution for message delivery.

``server_url``/``auth_token``/``user_id``/``webhook_url`` reuse the
scheduler's resolver (:func:`infrastructure.scheduling.scheduler.credentials.resolve_rocketchat_credentials`)
so task-params > integration-store > environment precedence stays identical
across the cron provider, Sentry digest, and alarm dispatchers. This module
adds channel resolution (explicit override -> store ``default_channel`` ->
``ROCKETCHAT_DEFAULT_CHANNEL`` env) and raises a setup-friendly error when
nothing usable is configured.

Routing rule (matches ``rocketchat_send_message``, the cron provider, and the
background-notify channel): token credentials with a channel are preferred;
the incoming webhook (fixed destination) is the fallback only when token
credentials are absent.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from config.constants.rocketchat import ROCKETCHAT_DEFAULT_CHANNEL_ENV
from infrastructure.errors import OpenSREError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RocketChatCredentials:
    """Resolved Rocket.Chat delivery target — token mode, webhook mode, or both.

    ``auth_token`` and ``webhook_url`` are excluded from the generated
    ``__repr__`` (the webhook URL embeds its own token) so neither leaks into
    pytest assertion output, tracebacks, or structured log capture.
    """

    server_url: str = ""
    auth_token: str = field(default="", repr=False)
    user_id: str = ""
    webhook_url: str = field(default="", repr=False)
    channel: str = ""

    @property
    def has_token(self) -> bool:
        return bool(self.server_url and self.auth_token and self.user_id)


def _store_default_channel() -> str:
    try:
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("rocketchat", {})
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
            "Failed to resolve Rocket.Chat default_channel from the store: %s",
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
    return os.getenv(ROCKETCHAT_DEFAULT_CHANNEL_ENV, "").strip()


def load_credentials_from_env(
    *,
    channel_override: str | None = None,
) -> RocketChatCredentials:
    """Resolve Rocket.Chat credentials for message delivery.

    Raises :class:`OpenSREError` with a setup-friendly suggestion when
    neither a usable token+channel combination nor a webhook is configured.
    """
    from infrastructure.scheduling.scheduler.credentials import resolve_rocketchat_credentials

    creds = resolve_rocketchat_credentials({})
    server_url = creds.get("server_url", "")
    auth_token = creds.get("auth_token", "")
    user_id = creds.get("user_id", "")
    webhook_url = creds.get("webhook_url", "")
    has_pat = bool(server_url and auth_token and user_id)
    channel = _resolve_channel(channel_override)

    if has_pat:
        if channel:
            return RocketChatCredentials(
                server_url=server_url,
                auth_token=auth_token,
                user_id=user_id,
                webhook_url=webhook_url,
                channel=channel,
            )
        raise OpenSREError(
            "Rocket.Chat has no channel to deliver to.",
            suggestion=(
                "Pass --chat-id, set a default channel during "
                "`opensre integrations setup rocketchat`, or export "
                "ROCKETCHAT_DEFAULT_CHANNEL=<channel>."
            ),
        )

    if webhook_url:
        # An explicit channel needs token credentials — the webhook's
        # destination is fixed at creation time and cannot honor one.
        stripped_override = channel_override.strip() if channel_override else ""
        if stripped_override:
            raise OpenSREError(
                "An explicit channel needs Rocket.Chat token credentials.",
                suggestion=(
                    "Configure server_url/auth_token/user_id with "
                    "`opensre integrations setup rocketchat`, or drop --chat-id "
                    "to use the incoming webhook's fixed destination."
                ),
            )
        return RocketChatCredentials(webhook_url=webhook_url)

    raise OpenSREError(
        "Rocket.Chat is not configured.",
        suggestion=(
            "Configure Rocket.Chat with `opensre integrations setup rocketchat` "
            "(or `opensre onboard`), or export ROCKETCHAT_SERVER_URL, "
            "ROCKETCHAT_AUTH_TOKEN, ROCKETCHAT_USER_ID (or ROCKETCHAT_WEBHOOK_URL)."
        ),
    )
