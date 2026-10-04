"""Telegram gateway configuration loaded from env and integration store."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.turn_host.concurrency import turn_limit_for_profile
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration

logger = logging.getLogger(__name__)


class GatewaySettings(StrictConfigModel):
    """Runtime settings for the Telegram gateway process."""

    bot_token: str
    allowed_user_ids: list[str] = Field(default_factory=list)
    max_concurrent_turns: int = Field(default_factory=turn_limit_for_profile, ge=1)
    stream_edit_interval_seconds: float = Field(default=1.5, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)
    shutdown_drain_seconds: float = Field(default=5.0, gt=0)
    auto_start_enabled: bool = True


class GatewayEnv(BaseSettings):
    """Environment-backed Telegram gateway settings."""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", extra="ignore")

    bot_token: str = ""
    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # CSV validator below can parse "42,99" instead of raising a SettingsError.
    allowed_users: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gateway_max_concurrent: int = Field(default_factory=turn_limit_for_profile, ge=1)
    gateway_stream_edit_interval_seconds: float = Field(default=1.5, gt=0)
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)
    gateway_auto_start: bool = True

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("gateway_auto_start", mode="before")
    @classmethod
    def parse_auto_start(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)


@dataclass(frozen=True)
class TelegramInboundMessage:
    """Normalized inbound Telegram DM text."""

    update_id: int
    user_id: str
    chat_id: str
    message_id: str
    text: str


@dataclass(frozen=True)
class TelegramCallbackQuery:
    """Normalized Approve/Deny (or other) inline-keyboard callback."""

    update_id: int
    user_id: str
    chat_id: str
    callback_query_id: str
    data: str


def load_telegram_credentials() -> Mapping[str, Any]:
    """Load Telegram credentials from the integration store."""

    try:
        record = get_integration(MessagingPlatform.TELEGRAM.value)
    except Exception as exc:
        raise GatewayConfigurationError("Could not load Telegram integration") from exc

    if not isinstance(record, Mapping):
        logger.info("Telegram integration not configured; using env only")
        return {}

    credentials = record.get("credentials")
    if not isinstance(credentials, Mapping):
        logger.info("Telegram integration has no credentials; using env only")
        return {}

    return credentials


def store_bot_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("bot_token") or "").strip()


def store_allowed_users(credentials: Mapping[str, Any]) -> list[str]:
    raw_policy = credentials.get("identity_policy")

    if not raw_policy:
        return []

    if not isinstance(raw_policy, Mapping):
        raise GatewayConfigurationError("Telegram identity_policy must be an object")

    try:
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Telegram identity_policy") from exc

    return list(policy.allowed_user_ids)


def choose_bot_token(env: GatewayEnv, credentials: Mapping[str, Any]) -> str:
    token = env.bot_token or store_bot_token(credentials)

    if not token:
        raise GatewayConfigurationError(
            "Telegram bot token is missing. Set TELEGRAM_BOT_TOKEN or configure the Telegram integration."
        )

    return token


def choose_authorized_users(env: GatewayEnv, credentials: Mapping[str, Any]) -> list[str]:
    users = store_allowed_users(credentials) or env.allowed_users

    if not users:
        logger.warning("Telegram allowed users are not configured")

    return users


def load_gateway_settings() -> GatewaySettings:
    """Load complete Telegram gateway settings."""

    try:
        env = GatewayEnv()
        credentials = load_telegram_credentials()

        return GatewaySettings(
            bot_token=choose_bot_token(env, credentials),
            allowed_user_ids=choose_authorized_users(env, credentials),
            max_concurrent_turns=env.gateway_max_concurrent,
            stream_edit_interval_seconds=env.gateway_stream_edit_interval_seconds,
            turn_timeout_seconds=env.gateway_turn_timeout_seconds,
            auto_start_enabled=env.gateway_auto_start,
        )
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Telegram gateway configuration") from exc
