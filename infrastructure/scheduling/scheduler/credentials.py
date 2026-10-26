"""Credential resolution for scheduled task delivery.

Resolves provider credentials from the integration store and environment
rather than requiring them to be stored in task params.

Secret env vars use ``resolve_env_credential`` (process env, then OS keyring)
so wizard ``sync_env_secret`` writes are visible when the store is empty.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from config.constants.buzz import (
    BUZZ_AUTH_TAG_ENV,
    BUZZ_DEFAULT_CHANNEL_ENV,
    BUZZ_PATH_ENV,
    BUZZ_PRIVATE_KEY_ENV,
    BUZZ_RELAY_URL_ENV,
)
from config.constants.discord import DISCORD_BOT_TOKEN_ENV
from config.constants.rocketchat import (
    ROCKETCHAT_AUTH_TOKEN_ENV,
    ROCKETCHAT_SERVER_URL_ENV,
    ROCKETCHAT_USER_ID_ENV,
)
from config.constants.slack import (
    SLACK_ACCESS_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_DEFAULT_CHAT_ID_ENV,
    SLACK_WEBHOOK_URL_ENV,
)
from config.constants.telegram import TELEGRAM_BOT_TOKEN_ENV, TELEGRAM_DEFAULT_CHAT_ID_ENV
from config.llm_credentials import resolve_env_credential
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_SLACK_CHAT_ID_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
)
from infrastructure.scheduling.scheduler.types import Provider

logger = logging.getLogger(__name__)


def resolve_telegram_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Telegram bot_token from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    """
    return _resolve_credentials(
        task_params,
        service="telegram",
        credential_key="bot_token",
        env_vars=(TELEGRAM_BOT_TOKEN_ENV,),
    )


def resolve_telegram_default_chat_id(task_params: dict[str, str] | None = None) -> str:
    """Resolve the default Telegram destination for scheduled delivery."""
    params = task_params or {}
    explicit = (
        params.get("chat_id", "").strip() or params.get(LOOP_TELEGRAM_CHAT_ID_PARAM, "").strip()
    )
    if explicit:
        return explicit

    store_chat_id = _get_integration_credential("telegram", "default_chat_id").strip()
    if store_chat_id:
        return store_chat_id
    return os.getenv(TELEGRAM_DEFAULT_CHAT_ID_ENV, "").strip()


def resolve_slack_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Slack credentials from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    Webhook URLs stay env/store only (not keyring-eligible as ``*_URL``).
    """
    creds, _source = _resolve_slack_with_source(task_params)
    return creds


def resolve_slack_default_chat_id(task_params: dict[str, str] | None = None) -> str:
    """Resolve the default Slack destination for scheduled bot-token delivery.

    Explicit ``chat_id`` / loop param always wins. Implicit defaults are
    source-aligned with the resolved bot token: a store channel is only used
    with a store token, and ``SLACK_DEFAULT_CHAT_ID`` only with an env/keyring
    token. A task-level token does not pick up a store or env default.
    """
    params = task_params or {}
    explicit = params.get("chat_id", "").strip() or params.get(LOOP_SLACK_CHAT_ID_PARAM, "").strip()
    if explicit:
        return explicit

    creds, source = _resolve_slack_with_source(params)
    if not creds.get("access_token"):
        return ""
    if source == "store":
        return _get_integration_credential("slack", "default_chat_id").strip()
    if source == "env":
        return os.getenv(SLACK_DEFAULT_CHAT_ID_ENV, "").strip()
    return ""


def _resolve_slack_with_source(task_params: dict[str, str]) -> tuple[dict[str, str], str]:
    """Return Slack creds plus the source they came from: params, store, env, or empty."""
    webhook_url = task_params.get("webhook_url", "").strip()
    if webhook_url:
        return {"webhook_url": webhook_url}, "params"

    access_token = task_params.get("access_token", "").strip()
    if access_token:
        return {"access_token": access_token}, "params"

    # Webhook: store then plain env — never resolve_env_credential / keyring.
    store_webhook = _get_integration_credential("slack", "webhook_url").strip()
    if store_webhook:
        return {"webhook_url": store_webhook}, "store"
    env_webhook = os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip()
    if env_webhook:
        return {"webhook_url": env_webhook}, "env"

    # Catalog / setup persist ``bot_token``; task params and some stores use
    # ``access_token``. Either is a store-sourced bot token.
    store_token = (
        _get_integration_credential("slack", "access_token").strip()
        or _get_integration_credential("slack", "bot_token").strip()
    )
    if store_token:
        return {"access_token": store_token}, "store"

    for env_var in (SLACK_BOT_TOKEN_ENV, SLACK_ACCESS_TOKEN_ENV):
        value = resolve_env_credential(env_var).strip()
        if value:
            return {"access_token": value}, "env"
    return {}, ""


def resolve_discord_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Discord bot_token from task params, integration store, env, or keyring.

    Priority: task.params > integration store > environment variable > system keyring.
    """
    return _resolve_credentials(
        task_params,
        service="discord",
        credential_key="bot_token",
        env_vars=(DISCORD_BOT_TOKEN_ENV,),
    )


def resolve_rocketchat_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Rocket.Chat credentials from task params, integration store, or env.

    Priority: task.params > integration store > environment variable (then keyring
    for the PAT), applied per key. Returns whichever of
    ``server_url``/``auth_token``/``user_id`` (token mode) and ``webhook_url``
    (webhook mode) could be resolved; the executor decides whether the
    combination is usable.

    Webhook URLs stay store/env only — never ``resolve_env_credential`` / keyring
    (same rule as Slack ``SLACK_WEBHOOK_URL``).
    """
    resolved: dict[str, str] = {}

    # Non-secret / non-keyring fields: params → store → plain env.
    for key, env_var in (
        ("server_url", ROCKETCHAT_SERVER_URL_ENV),
        ("user_id", ROCKETCHAT_USER_ID_ENV),
    ):
        value = task_params.get(key, "").strip()
        if not value:
            value = _get_integration_credential("rocketchat", key).strip()
        if not value:
            value = os.getenv(env_var, "").strip()
        if value:
            resolved[key] = value

    # PAT: params → store → env then keyring.
    auth = _resolve_credentials(
        task_params,
        service="rocketchat",
        credential_key="auth_token",
        env_vars=(ROCKETCHAT_AUTH_TOKEN_ENV,),
    )
    resolved.update(auth)

    # Webhook: params → store → plain env only.
    webhook_url = task_params.get("webhook_url", "").strip()
    if not webhook_url:
        webhook_url = _get_integration_credential("rocketchat", "webhook_url").strip()
    if not webhook_url:
        webhook_url = os.getenv("ROCKETCHAT_WEBHOOK_URL", "").strip()
    if webhook_url:
        resolved["webhook_url"] = webhook_url

    return resolved


def resolve_buzz_credentials(task_params: dict[str, str]) -> dict[str, str]:
    """Resolve Buzz credentials from task params, integration store, or env.

    Priority: task.params > integration store > environment variable (then
    keyring for ``private_key``), applied per key. Returns whichever of
    ``private_key``/``relay_url``/``default_channel``/``auth_tag``/``buzz_path``
    could be resolved; the caller decides whether the combination is usable.
    """
    resolved: dict[str, str] = {}

    # Non-secret fields: params → store → plain env.
    for key, env_var in (
        ("relay_url", BUZZ_RELAY_URL_ENV),
        ("default_channel", BUZZ_DEFAULT_CHANNEL_ENV),
        ("auth_tag", BUZZ_AUTH_TAG_ENV),
        ("buzz_path", BUZZ_PATH_ENV),
    ):
        value = task_params.get(key, "").strip()
        if not value:
            value = _get_integration_credential("buzz", key).strip()
        if not value:
            value = os.getenv(env_var, "").strip()
        if value:
            resolved[key] = value

    # Private key: params → store → env then keyring.
    private_key = _resolve_credentials(
        task_params,
        service="buzz",
        credential_key="private_key",
        env_vars=(BUZZ_PRIVATE_KEY_ENV,),
    )
    resolved.update(private_key)

    return resolved


def _resolve_credentials(
    task_params: dict[str, str],
    *,
    service: str,
    credential_key: str,
    env_vars: tuple[str, ...],
) -> dict[str, str]:
    """Resolve a single credential from task params, store, env, or keyring."""
    value = task_params.get(credential_key, "")
    if value:
        return {credential_key: value}

    value = _get_integration_credential(service, credential_key)
    if value:
        return {credential_key: value}

    for env_var in env_vars:
        value = resolve_env_credential(env_var).strip()
        if value:
            return {credential_key: value}

    return {}


def _get_integration_credential(service: str, key: str) -> str:
    """Look up a credential from the integration store."""
    try:
        from integrations.catalog import resolve_effective_integrations

        integrations = resolve_effective_integrations()
        integration: dict[str, Any] = integrations.get(service, {})
        if not isinstance(integration, dict):
            return ""
        config = integration.get("config", {})
        if not isinstance(config, dict):
            return ""
        value = config.get(key, "")
        return str(value) if value else ""
    except Exception:
        logger.debug("Failed to resolve %s credential from integration store", service)
        return ""


def requires_explicit_chat_id(provider: str, task_params: dict[str, str] | None = None) -> bool:
    """Whether a scheduled task needs an explicit ``chat_id`` to be deliverable.

    Mirrors what :func:`infrastructure.scheduling.scheduler.executor._deliver_slack` actually
    does: a Slack webhook is bound to one channel and is its own destination,
    so it can deliver without a chat id. A bot token cannot — it posts to a
    named channel. Interactive-shell delivery writes to the local loop inbox
    and never needs a chat id. Accepting a task without a reachable destination
    stores a schedule that fires into nothing.
    """
    provider_name = provider.strip().lower()
    if provider_name == Provider.INTERACTIVE_SHELL.value:
        return False
    if provider_name != Provider.SLACK.value:
        return True
    creds = resolve_slack_credentials(task_params or {})
    if creds.get("webhook_url", "").strip():
        return False
    return not resolve_slack_default_chat_id(task_params or {}).strip()


__all__ = [
    "requires_explicit_chat_id",
    "resolve_discord_credentials",
    "resolve_rocketchat_credentials",
    "resolve_slack_credentials",
    "resolve_slack_default_chat_id",
    "resolve_telegram_default_chat_id",
    "resolve_telegram_credentials",
]
