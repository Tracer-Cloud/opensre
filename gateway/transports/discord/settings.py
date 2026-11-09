"""Discord gateway configuration loaded from env and integration store."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.constants.discord import DISCORD_ALLOW_OPEN_GUILD_ENV, DISCORD_ALLOWED_USERS_ENV
from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.turn_host.concurrency import turn_limit_for_profile
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration

logger = logging.getLogger(__name__)


class DiscordGatewaySettings(StrictConfigModel):
    """Runtime settings for the Discord gateway worker."""

    bot_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)
    allow_open_guild: bool = False
    max_concurrent_turns: int = Field(default_factory=turn_limit_for_profile, ge=1)
    status_update_interval_seconds: float = Field(default=2.0, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)
    startup_timeout_seconds: float = Field(default=30.0, gt=0)


class DiscordGatewayEnv(BaseSettings):
    """Environment-backed Discord gateway settings."""

    model_config = SettingsConfigDict(env_prefix="DISCORD_", extra="ignore")

    bot_token: str = ""
    allowed_users: Annotated[list[str], NoDecode] = Field(default_factory=list)
    allow_open_guild: bool = False
    gateway_max_concurrent: int = Field(default_factory=turn_limit_for_profile, ge=1)
    gateway_status_update_interval_seconds: float = Field(default=2.0, gt=0)
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)
    gateway_startup_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_discord_credentials() -> Mapping[str, Any]:
    """Load Discord credentials from the integration store."""
    try:
        record = get_integration(MessagingPlatform.DISCORD.value)
    except Exception as exc:
        raise GatewayConfigurationError("Could not load Discord integration") from exc

    if not isinstance(record, Mapping):
        logger.info("Discord integration not configured; using env only")
        return {}

    credentials = record.get("credentials")
    if not isinstance(credentials, Mapping):
        logger.info("Discord integration has no credentials; using env only")
        return {}

    return credentials


def store_bot_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("bot_token") or "").strip()


def store_allowed_users(credentials: Mapping[str, Any]) -> list[str]:
    raw_policy = credentials.get("identity_policy")
    if not raw_policy:
        return []
    if not isinstance(raw_policy, Mapping):
        raise GatewayConfigurationError("Discord identity_policy must be an object")
    try:
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Discord identity_policy") from exc
    return list(policy.allowed_user_ids)


def choose_bot_token(env: DiscordGatewayEnv, credentials: Mapping[str, Any]) -> str:
    token = env.bot_token or store_bot_token(credentials)
    if not token:
        raise GatewayConfigurationError(
            "Discord bot token is missing. Set DISCORD_BOT_TOKEN or configure the Discord integration."
        )
    return token


def choose_authorized_users(env: DiscordGatewayEnv, credentials: Mapping[str, Any]) -> list[str]:
    return store_allowed_users(credentials) or env.allowed_users


def load_discord_gateway_settings() -> DiscordGatewaySettings:
    """Load complete Discord gateway settings from env and the integration store."""
    try:
        env = DiscordGatewayEnv()
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Discord gateway configuration") from exc

    credentials = load_discord_credentials()
    allowed_users = choose_authorized_users(env, credentials)

    if not allowed_users and not env.allow_open_guild:
        raise GatewayConfigurationError(
            "Discord gateway needs allowed users: run `opensre messaging allow -p discord -u <id>`, "
            f"set {DISCORD_ALLOWED_USERS_ENV} (comma-separated user IDs), "
            f"or set {DISCORD_ALLOW_OPEN_GUILD_ENV}=1 to allow any guild member (dogfood only)."
        )

    if env.allow_open_guild and not allowed_users:
        logger.warning(
            "%s=1: any guild member can talk to the bot in server channels "
            "(DMs still require the allowlist)",
            DISCORD_ALLOW_OPEN_GUILD_ENV,
        )

    return DiscordGatewaySettings(
        bot_token=choose_bot_token(env, credentials),
        allowed_user_ids=allowed_users,
        allow_open_guild=env.allow_open_guild,
        max_concurrent_turns=env.gateway_max_concurrent,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
        turn_timeout_seconds=env.gateway_turn_timeout_seconds,
        startup_timeout_seconds=env.gateway_startup_timeout_seconds,
    )
