"""Buzz gateway configuration loaded from env and the integration store."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from config.strict_config import StrictConfigModel
from gateway.core.lifecycle.errors import GatewayConfigurationError
from infrastructure.scheduling.scheduler.credentials import resolve_buzz_credentials
from integrations.messaging_security import MessagingIdentityPolicy, MessagingPlatform
from integrations.store import get_integration

logger = logging.getLogger(__name__)


class GatewaySettings(StrictConfigModel):
    """Runtime settings for the Buzz gateway process."""

    private_key: str
    relay_url: str = "http://localhost:3000"
    auth_tag: str = ""
    buzz_path: str = "buzz"
    allowed_pubkeys: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=15.0, gt=0)
    stream_edit_interval_seconds: float = Field(default=1.5, gt=0)
    max_concurrent_turns: int = Field(default=4, ge=1)
    # How long shutdown lets in-flight turns finish. Kept under the gateway's
    # own 8s thread join: pending approvals are denied before the drain starts,
    # so turns return promptly, and anything slower is re-delivered next start
    # rather than waited on.
    shutdown_drain_seconds: float = Field(default=5.0, gt=0)
    turn_timeout_seconds: float = Field(default=240.0, gt=0)
    auto_start_enabled: bool = True


class BuzzGatewayEnv(BaseSettings):
    """Environment-backed Buzz gateway settings.

    ``private_key``/``relay_url``/``auth_tag``/``buzz_path`` are resolved via
    :func:`infrastructure.scheduling.scheduler.credentials.resolve_buzz_credentials` instead of
    duplicated here, so the store > env > keyring precedence stays identical
    to the shared delivery configuration path.
    """

    model_config = SettingsConfigDict(env_prefix="BUZZ_", extra="ignore")

    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # CSV validator below can parse a comma-separated pubkey list.
    allowed_pubkeys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    gateway_poll_interval_seconds: float = Field(default=15.0, gt=0)
    gateway_max_concurrent: int = Field(default=4, ge=1)
    gateway_stream_edit_interval_seconds: float = Field(default=1.5, gt=0)
    gateway_turn_timeout_seconds: float = Field(default=240.0, gt=0)
    gateway_auto_start: bool = True

    @field_validator("allowed_pubkeys", mode="before")
    @classmethod
    def parse_allowed_pubkeys(cls, value: Any) -> Any:
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
class BuzzInboundMessage:
    """Normalized inbound Buzz mention/reply event (a ``feed get`` row)."""

    event_id: str
    pubkey: str
    channel_id: str
    content: str
    created_at: int
    # Every event-id this message's ``e`` tags reference — used to recognize a
    # reply to a pending approval prompt without full NIP-10 thread resolution.
    reply_event_ids: frozenset[str]


def load_buzz_credentials() -> Mapping[str, Any]:
    """Load Buzz credentials from the integration store."""
    try:
        record = get_integration(MessagingPlatform.BUZZ.value)
    except Exception as exc:
        raise GatewayConfigurationError("Could not load Buzz integration") from exc

    if not isinstance(record, Mapping):
        logger.info("Buzz integration not configured; using env only")
        return {}

    credentials = record.get("credentials")
    if not isinstance(credentials, Mapping):
        logger.info("Buzz integration has no credentials; using env only")
        return {}

    return credentials


def store_allowed_pubkeys(credentials: Mapping[str, Any]) -> list[str]:
    raw_policy = credentials.get("identity_policy")

    if not raw_policy:
        return []

    if not isinstance(raw_policy, Mapping):
        raise GatewayConfigurationError("Buzz identity_policy must be an object")

    try:
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Buzz identity_policy") from exc

    return list(policy.allowed_user_ids)


def choose_allowed_pubkeys(env: BuzzGatewayEnv, credentials: Mapping[str, Any]) -> list[str]:
    pubkeys = store_allowed_pubkeys(credentials) or env.allowed_pubkeys

    if not pubkeys:
        logger.warning("Buzz allowed pubkeys are not configured")

    return pubkeys


def load_gateway_settings() -> GatewaySettings:
    """Load complete Buzz gateway settings."""

    try:
        env = BuzzGatewayEnv()
        creds = resolve_buzz_credentials({})
        private_key = creds.get("private_key", "")
        if not private_key:
            raise GatewayConfigurationError(
                "Buzz private key is missing. Set BUZZ_PRIVATE_KEY or configure the "
                "Buzz integration."
            )
        credentials = load_buzz_credentials()

        return GatewaySettings(
            private_key=private_key,
            relay_url=creds.get("relay_url", "") or "http://localhost:3000",
            auth_tag=creds.get("auth_tag", ""),
            buzz_path=creds.get("buzz_path", "") or "buzz",
            allowed_pubkeys=choose_allowed_pubkeys(env, credentials),
            poll_interval_seconds=env.gateway_poll_interval_seconds,
            stream_edit_interval_seconds=env.gateway_stream_edit_interval_seconds,
            turn_timeout_seconds=env.gateway_turn_timeout_seconds,
            max_concurrent_turns=env.gateway_max_concurrent,
            auto_start_enabled=env.gateway_auto_start,
        )
    except ValidationError as exc:
        raise GatewayConfigurationError("Invalid Buzz gateway configuration") from exc


__all__ = [
    "BuzzGatewayEnv",
    "BuzzInboundMessage",
    "GatewaySettings",
    "load_buzz_credentials",
    "load_gateway_settings",
]
