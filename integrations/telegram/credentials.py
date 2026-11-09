"""Telegram credential resolution shared by every caller that posts to Telegram.

Resolution rule:

* **bot token** — integration store -> ``TELEGRAM_BOT_TOKEN`` env -> system keyring
* **chat id** — explicit override -> integration store ``default_chat_id`` ->
  ``TELEGRAM_DEFAULT_CHAT_ID`` env

The store, env, and keyring fallbacks match the resolution order the scheduler
and onboarding wizard use, so credentials saved via ``opensre onboard`` or
``opensre integrations setup telegram`` work uniformly across the Telegram
send-message tool and any other caller.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from config.constants.telegram import TELEGRAM_BOT_TOKEN_ENV, TELEGRAM_DEFAULT_CHAT_ID_ENV
from infrastructure.errors import OpenSREError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramCredentials:
    """Resolved Telegram bot token + target chat id."""

    # repr=False so the auto-generated __repr__ does not leak the token into
    # pytest assertion output, tracebacks, or structured log capture.
    bot_token: str = field(repr=False)
    chat_id: str = field()


def _telegram_store_config() -> dict[str, object]:
    """Return the Telegram integration's effective config, or ``{}``.

    Reads the merged integration store + environment view used everywhere else
    (agent turns, scheduler). Returns an empty mapping when the store
    is unavailable or has no Telegram integration so callers fall back to the
    environment / keyring. Resolution is wrapped defensively: a malformed or
    locked store must never crash the caller at startup.
    """
    try:
        from integrations.catalog import resolve_effective_integrations

        entry = resolve_effective_integrations().get("telegram", {})
        config = entry.get("config", {}) if isinstance(entry, dict) else {}
        return config if isinstance(config, dict) else {}
    except (ImportError, KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        logger.debug(
            "Failed to resolve Telegram credentials from the store: %s", exc, exc_info=True
        )
        return {}


def _resolve_bot_token(store_config: dict[str, object]) -> str:
    """Resolve the bot token: store first, then ``TELEGRAM_BOT_TOKEN`` env, then keyring."""
    store_token = str(store_config.get("bot_token") or "").strip()
    if store_token:
        return store_token
    # resolve_env_credential checks the environment first, then the system
    # keyring — so guided setup (which stores the token in the keyring) works.
    from config.llm_credentials import resolve_env_credential

    return resolve_env_credential(TELEGRAM_BOT_TOKEN_ENV).strip()


def _resolve_chat_id(store_config: dict[str, object], chat_id_override: str | None) -> str:
    """Resolve the chat id: explicit override, then store, then env."""
    # Strip first so a whitespace-only override falls back consistently with an
    # empty-string override, instead of raising a misleading "pass --chat-id"
    # error after the caller already passed one.
    stripped_override = chat_id_override.strip() if chat_id_override else ""
    if stripped_override:
        return stripped_override
    store_chat_id = str(store_config.get("default_chat_id") or "").strip()
    if store_chat_id:
        return store_chat_id
    return os.getenv(TELEGRAM_DEFAULT_CHAT_ID_ENV, "").strip()


def load_credentials_from_env(
    *,
    chat_id_override: str | None = None,
) -> TelegramCredentials:
    """Resolve Telegram credentials from the integration store, env, or keyring.

    Raises :class:`OpenSREError` with a setup-friendly suggestion when either
    half is missing.
    """
    store_config = _telegram_store_config()

    bot_token = _resolve_bot_token(store_config)
    if not bot_token:
        raise OpenSREError(
            "TELEGRAM_BOT_TOKEN is not set.",
            suggestion=(
                "Configure Telegram with `opensre integrations setup telegram` "
                "or export TELEGRAM_BOT_TOKEN=<your-bot-token>. "
                "Get a token from @BotFather on Telegram."
            ),
        )

    chat_id = _resolve_chat_id(store_config, chat_id_override)
    if not chat_id:
        raise OpenSREError(
            "Telegram chat id is not set.",
            suggestion=(
                "Set a default chat id during `opensre integrations setup telegram`, "
                "export TELEGRAM_DEFAULT_CHAT_ID=<chat-id>, or pass --chat-id and retry."
            ),
        )

    return TelegramCredentials(bot_token=bot_token, chat_id=chat_id)
