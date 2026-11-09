"""Slack gateway configuration loaded from env and integration store."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.turn_host.concurrency import turn_limit_for_profile
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration

logger = logging.getLogger(__name__)


class SlackInboundTransport(StrEnum):
    """How Slack delivers inbound events to this gateway."""

    #: One WebSocket held by the process. Single-consumer: a second
    #: connection splits the event stream, so this cannot scale out.
    SOCKET_MODE = "socket_mode"
    #: Signed POSTs to a public URL. Any replica can serve one.
    EVENTS_API_HTTP = "events_api_http"


class SlackGatewaySettings(StrictConfigModel):
    """Runtime settings for the Slack Socket Mode gateway."""

    bot_token: str
    # Socket Mode only; empty under Events API HTTP.
    app_token: str = ""
    # Events API HTTP only; empty under Socket Mode.
    signing_secret: str = ""
    inbound_transport: SlackInboundTransport = SlackInboundTransport.SOCKET_MODE
    # Events API HTTP only: port the Slack listener binds.
    http_port: int = Field(default=3000, ge=1, le=65_535)
    allowed_user_ids: list[str] = Field(default_factory=list)
    allow_open_workspace: bool = False
    max_concurrent_turns: int = Field(default_factory=turn_limit_for_profile, ge=1)
    # Slack's AI-app guidance: call chat.update at most once every 3 seconds.
    status_update_interval_seconds: float = Field(default=3.0, gt=0)
    # A GitHub fix-and-merge turn can run well past 15 minutes. The old 4-minute
    # cutoff posted "try again" in the thread and dropped the real answer.
    turn_timeout_seconds: float = Field(default=1800.0, gt=0)
    # Empty uses the worker's default path; the container health check reads it.
    heartbeat_path: str = ""


class SlackGatewayEnv(BaseSettings):
    """Environment-backed Slack gateway settings.

    Tokens must come from the environment / secret store — never commit them.
    """

    model_config = SettingsConfigDict(env_prefix="SLACK_", extra="ignore")

    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    gateway_inbound_transport: SlackInboundTransport = SlackInboundTransport.SOCKET_MODE
    gateway_http_port: int = Field(default=3000, ge=1, le=65_535)
    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # CSV validator below can parse "U123,U456" instead of raising a SettingsError.
    allowed_users: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Explicit escape hatch only — empty allowlist alone must not open the bot.
    allow_open_workspace: bool = False
    gateway_max_concurrent: int = Field(default_factory=turn_limit_for_profile, ge=1)
    gateway_status_update_interval_seconds: float = Field(default=3.0, gt=0)
    gateway_turn_timeout_seconds: float = Field(default=1800.0, gt=0)
    gateway_heartbeat_path: str = ""

    @field_validator("allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def load_slack_credentials() -> Mapping[str, Any]:
    """Load Slack credentials from the integration store."""

    try:
        record = get_integration(MessagingPlatform.SLACK.value)
    except Exception as exc:
        raise GatewayConfigurationError("Could not load Slack integration") from exc

    if not isinstance(record, Mapping):
        logger.info("Slack integration not configured; using env only")
        return {}

    credentials = record.get("credentials")
    if not isinstance(credentials, Mapping):
        logger.info("Slack integration has no credentials; using env only")
        return {}

    return credentials


def store_bot_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("bot_token") or "").strip()


def store_app_token(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("app_token") or "").strip()


def store_allowed_users(credentials: Mapping[str, Any]) -> list[str]:
    raw_policy = credentials.get("identity_policy")

    if not raw_policy:
        return []

    if not isinstance(raw_policy, Mapping):
        raise GatewayConfigurationError("Slack identity_policy must be an object")

    try:
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Slack identity_policy") from exc

    return list(policy.allowed_user_ids)


def choose_bot_token(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> str:
    token = env.bot_token or store_bot_token(credentials)

    if not token:
        raise GatewayConfigurationError(
            "Slack bot token is missing. Set SLACK_BOT_TOKEN (xoxb-…) or configure "
            "the Slack integration."
        )

    return token


def store_signing_secret(credentials: Mapping[str, Any]) -> str:
    return str(credentials.get("signing_secret") or "").strip()


def choose_app_token(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> str:
    """Socket Mode app-level token; empty is valid under Events API HTTP."""
    return env.app_token or store_app_token(credentials)


def choose_signing_secret(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> str:
    """Events API HMAC secret; empty is valid under Socket Mode."""
    return env.signing_secret or store_signing_secret(credentials)


# The credential each inbound transport cannot start without, and how to supply
# it. A table rather than a branch chain: adding a transport adds a row.
_REQUIRED_CREDENTIAL: Mapping[SlackInboundTransport, tuple[str, str]] = {
    SlackInboundTransport.SOCKET_MODE: (
        "app_token",
        "Slack app-level token is missing. Set SLACK_APP_TOKEN (xapp-…) or configure "
        "the Slack integration.",
    ),
    SlackInboundTransport.EVENTS_API_HTTP: (
        "signing_secret",
        "Slack signing secret is missing. Set SLACK_SIGNING_SECRET or configure the "
        "Slack integration — Events API HTTP cannot verify request signatures "
        "without it.",
    ),
}


def require_transport_credential(
    transport: SlackInboundTransport,
    *,
    app_token: str,
    signing_secret: str,
) -> None:
    """Raise when the selected transport is missing the credential it needs."""
    field, guidance = _REQUIRED_CREDENTIAL[transport]
    supplied = {"app_token": app_token, "signing_secret": signing_secret}
    if not supplied[field]:
        raise GatewayConfigurationError(guidance)


def choose_authorized_users(env: SlackGatewayEnv, credentials: Mapping[str, Any]) -> list[str]:
    return store_allowed_users(credentials) or env.allowed_users


def load_slack_gateway_settings() -> SlackGatewaySettings:
    """Load complete Slack gateway settings from env and the integration store."""

    try:
        env = SlackGatewayEnv()
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Slack gateway configuration") from exc

    credentials = load_slack_credentials()
    allowed_users = choose_authorized_users(env, credentials)

    if not allowed_users and not env.allow_open_workspace:
        raise GatewayConfigurationError(
            "Slack gateway needs allowed users: run `opensre messaging allow -p slack -u <id>`, "
            "set SLACK_ALLOWED_USERS (comma-separated user IDs), "
            "or set SLACK_ALLOW_OPEN_WORKSPACE=1 to allow any workspace member (dogfood only)."
        )

    if env.allow_open_workspace and not allowed_users:
        logger.warning("SLACK_ALLOW_OPEN_WORKSPACE=1: any workspace member can talk to the bot")

    app_token = choose_app_token(env, credentials)
    signing_secret = choose_signing_secret(env, credentials)
    require_transport_credential(
        env.gateway_inbound_transport,
        app_token=app_token,
        signing_secret=signing_secret,
    )

    return SlackGatewaySettings(
        bot_token=choose_bot_token(env, credentials),
        app_token=app_token,
        signing_secret=signing_secret,
        inbound_transport=env.gateway_inbound_transport,
        http_port=env.gateway_http_port,
        allowed_user_ids=allowed_users,
        allow_open_workspace=env.allow_open_workspace,
        max_concurrent_turns=env.gateway_max_concurrent,
        status_update_interval_seconds=env.gateway_status_update_interval_seconds,
        turn_timeout_seconds=env.gateway_turn_timeout_seconds,
        heartbeat_path=env.gateway_heartbeat_path,
    )
