"""What Telegram needs before it is considered configured.

Both credentials are required. A bot token alone verifies successfully, but
the send-message tool resolves a chat id through
:func:`integrations.telegram.credentials.load_credentials_from_env` and raises
without one. Accepting a token-only setup produces an integration that looks
healthy in ``opensre integrations list`` and fails at the first delivery.

The chat id is resolved rather than trusted: users can supply the ``@username``
of a public channel, which is far easier to obtain than a numeric id, and
:mod:`integrations.telegram.chat_lookup` turns it into the numeric id delivery
uses — failing setup if the bot cannot reach that chat at all.
"""

from __future__ import annotations

from config.constants.telegram import TELEGRAM_BOT_TOKEN_ENV, TELEGRAM_DEFAULT_CHAT_ID_ENV
from integrations.setup_flow import IntegrationSetupSpec, ResolvedCredentials, SetupField
from integrations.telegram.chat_lookup import resolve_chat_id
from integrations.telegram.setup_guide import guide_telegram
from integrations.telegram.verifier import verify_telegram

BOT_TOKEN_FIELD = "bot_token"
DEFAULT_CHAT_ID_FIELD = "default_chat_id"


def _resolve_default_chat_id(credentials: dict[str, str | None]) -> ResolvedCredentials:
    """Replace the submitted chat reference with its numeric id."""
    numeric_id, description, error = resolve_chat_id(
        bot_token=str(credentials.get(BOT_TOKEN_FIELD) or ""),
        chat_id=str(credentials.get(DEFAULT_CHAT_ID_FIELD) or ""),
    )
    if error:
        return ResolvedCredentials(credentials={}, error=error)
    return ResolvedCredentials(
        credentials={**credentials, DEFAULT_CHAT_ID_FIELD: numeric_id},
        note=f"Delivering to {description}.",
    )


TELEGRAM_SETUP = IntegrationSetupSpec(
    service="telegram",
    guide=guide_telegram,
    fields=(
        SetupField(
            name=BOT_TOKEN_FIELD,
            label="Telegram bot token",
            env_var=TELEGRAM_BOT_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=DEFAULT_CHAT_ID_FIELD,
            label="Default chat ID",
            prompt="Default chat ID or @channelname",
            env_var=TELEGRAM_DEFAULT_CHAT_ID_ENV,
        ),
    ),
    verify=verify_telegram,
    resolve=_resolve_default_chat_id,
)

__all__ = [
    "BOT_TOKEN_FIELD",
    "DEFAULT_CHAT_ID_FIELD",
    "TELEGRAM_SETUP",
]
