"""Shared strict models for normalized integration configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from app.config import get_tracer_base_url
from app.strict_config import StrictConfigModel

_LOCAL_GRAFANA_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


class GrafanaIntegrationConfig(StrictConfigModel):
    """Normalized Grafana credentials used by resolution and verification flows."""

    endpoint: str
    api_key: str = ""
    integration_id: str = ""

    @field_validator("endpoint", mode="before")
    @classmethod
    def _normalize_endpoint(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @property
    def is_local(self) -> bool:
        host = urlparse(self.endpoint).hostname or ""
        return host in _LOCAL_GRAFANA_HOSTS


class DatadogIntegrationConfig(StrictConfigModel):
    """Normalized Datadog credentials used by resolution and verification flows."""

    api_key: str
    app_key: str
    site: str = "datadoghq.com"
    integration_id: str = ""


class AWSStaticCredentials(StrictConfigModel):
    """Static AWS access key credentials."""

    access_key_id: str
    secret_access_key: str
    session_token: str = ""


class AWSIntegrationConfig(StrictConfigModel):
    """Normalized AWS integration config supporting role or static keys."""

    region: str = "us-east-1"
    role_arn: str = ""
    external_id: str = ""
    credentials: AWSStaticCredentials | None = None
    integration_id: str = ""

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> str:
        return str(value or "us-east-1").strip() or "us-east-1"

    @model_validator(mode="after")
    def _require_auth_method(self) -> AWSIntegrationConfig:
        if self.role_arn or self.credentials:
            return self
        raise ValueError(
            "AWS integration requires either role_arn or credentials.access_key_id/secret_access_key."
        )


class SlackWebhookConfig(StrictConfigModel):
    """Slack webhook runtime config."""

    webhook_url: str

    @model_validator(mode="after")
    def _require_https_slack_url(self) -> SlackWebhookConfig:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Slack webhook must be a valid HTTPS URL.")
        if "slack.com" not in parsed.netloc:
            raise ValueError("Slack webhook host must be a Slack domain.")
        return self


class TracerIntegrationConfig(StrictConfigModel):
    """Tracer API access config."""

    base_url: str = Field(default_factory=get_tracer_base_url)
    jwt_token: str

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or get_tracer_base_url()).strip() or get_tracer_base_url()

    @field_validator("jwt_token", mode="before")
    @classmethod
    def _normalize_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token


class EffectiveIntegrationEntry(StrictConfigModel):
    """Resolved integration entry with source metadata."""

    source: str
    config: dict[str, Any]


class EffectiveIntegrations(StrictConfigModel):
    """Strict container for normalized effective integrations."""

    grafana: EffectiveIntegrationEntry | None = None
    datadog: EffectiveIntegrationEntry | None = None
    aws: EffectiveIntegrationEntry | None = None
    slack: EffectiveIntegrationEntry | None = None
    tracer: EffectiveIntegrationEntry | None = None
    github: EffectiveIntegrationEntry | None = None
    sentry: EffectiveIntegrationEntry | None = None
