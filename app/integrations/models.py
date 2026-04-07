"""Shared strict models for normalized integration configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from app.config import get_tracer_base_url
from app.strict_config import StrictConfigModel

_LOCAL_GRAFANA_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
DEFAULT_HONEYCOMB_BASE_URL = "https://api.honeycomb.io"
DEFAULT_HONEYCOMB_DATASET = "__all__"
DEFAULT_CORALOGIX_BASE_URL = "https://api.coralogix.com"


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


class HoneycombIntegrationConfig(StrictConfigModel):
    """Normalized Honeycomb credentials used by resolution and verification flows."""

    api_key: str
    dataset: str = DEFAULT_HONEYCOMB_DATASET
    base_url: str = DEFAULT_HONEYCOMB_BASE_URL
    integration_id: str = ""

    @field_validator("dataset", mode="before")
    @classmethod
    def _normalize_dataset(cls, value: object) -> str:
        return str(value or DEFAULT_HONEYCOMB_DATASET).strip() or DEFAULT_HONEYCOMB_DATASET

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or DEFAULT_HONEYCOMB_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_HONEYCOMB_BASE_URL


class CoralogixIntegrationConfig(StrictConfigModel):
    """Normalized Coralogix credentials used by resolution and verification flows."""

    api_key: str
    base_url: str = DEFAULT_CORALOGIX_BASE_URL
    application_name: str = ""
    subsystem_name: str = ""
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or DEFAULT_CORALOGIX_BASE_URL).strip().rstrip("/")
        return normalized or DEFAULT_CORALOGIX_BASE_URL


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


class JiraIntegrationConfig(StrictConfigModel):
    """Normalized Jira credentials used by resolution and verification flows."""

    base_url: str
    email: str
    api_token: str
    project_key: str
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("email", "api_token", "project_key", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def auth(self) -> tuple[str, str]:
        return (self.email, self.api_token)

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/rest/api/3"


class MongoDBIntegrationConfig(StrictConfigModel):
    """Normalized MongoDB credentials used by resolution and verification flows."""

    connection_string: str
    database: str = ""
    auth_source: str = "admin"
    tls: bool = True
    integration_id: str = ""

    @field_validator("connection_string", mode="before")
    @classmethod
    def _normalize_connection_string(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("auth_source", mode="before")
    @classmethod
    def _normalize_auth_source(cls, value: object) -> str:
        normalized = str(value or "admin").strip()
        return normalized or "admin"


class MongoDBAtlasIntegrationConfig(StrictConfigModel):
    """Normalized MongoDB Atlas API credentials used by resolution and verification flows."""

    api_public_key: str
    api_private_key: str
    project_id: str
    base_url: str = "https://cloud.mongodb.com/api/atlas/v2"
    integration_id: str = ""

    @field_validator("api_public_key", "api_private_key", "project_id", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or "https://cloud.mongodb.com/api/atlas/v2").strip().rstrip("/")
        return normalized or "https://cloud.mongodb.com/api/atlas/v2"


class GoogleDocsIntegrationConfig(StrictConfigModel):
    """Normalized Google Docs (Drive API) credentials for incident report generation."""

    credentials_file: str
    folder_id: str
    integration_id: str = ""
    timeout_seconds: int = 30

    @field_validator("credentials_file", mode="before")
    @classmethod
    def _normalize_credentials_file(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _validate_timeout(cls, value: object) -> int:
        """Validate timeout is a positive integer with reasonable bounds."""
        # Handle string or numeric input
        if isinstance(value, str):
            try:
                timeout = int(value)
            except ValueError:
                return 30
        elif isinstance(value, (int, float)):
            timeout = int(value)
        else:
            return 30
        # Enforce reasonable bounds: 5 seconds minimum, 300 seconds maximum
        return max(5, min(timeout, 300))

class GitLabIntegrationConfig(StrictConfigModel):
    """Normalized Gitlab credentials used by resolution and verification flows."""

    url: str
    access_token: str
    integration_id: str = ""

class OpsGenieIntegrationConfig(StrictConfigModel):
    """Normalized OpsGenie credentials used by resolution and verification flows."""

    api_key: str
    region: str = "us"
    integration_id: str = ""

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> str:
        raw = str(value or "us").strip().lower()
        return raw if raw in ("us", "eu") else "us"


class PrefectIntegrationConfig(StrictConfigModel):
    api_url: str = "https://api.prefect.cloud/api"
    api_key: str = ""
    account_id: str = ""
    workspace_id: str = ""
    integration_id: str = ""

    @field_validator("api_url", mode="before")
    @classmethod
    def _normalize_api_url(cls, value: object) -> str:
        return str(value or "https://api.prefect.cloud/api").strip().rstrip("/")

    @field_validator("api_key", "account_id", "workspace_id", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()


class EffectiveIntegrationEntry(StrictConfigModel):
    """Resolved integration entry with source metadata."""

    source: str
    config: dict[str, Any]


class EffectiveIntegrations(StrictConfigModel):
    """Strict container for normalized effective integrations."""

    grafana: EffectiveIntegrationEntry | None = None
    datadog: EffectiveIntegrationEntry | None = None
    honeycomb: EffectiveIntegrationEntry | None = None
    coralogix: EffectiveIntegrationEntry | None = None
    aws: EffectiveIntegrationEntry | None = None
    slack: EffectiveIntegrationEntry | None = None
    tracer: EffectiveIntegrationEntry | None = None
    github: EffectiveIntegrationEntry | None = None
    sentry: EffectiveIntegrationEntry | None = None
    mongodb: EffectiveIntegrationEntry | None = None
    mongodb_atlas: EffectiveIntegrationEntry | None = None
    google_docs: EffectiveIntegrationEntry | None = None
    gitlab: EffectiveIntegrationEntry | None = None
    vercel: EffectiveIntegrationEntry | None = None
    jira: EffectiveIntegrationEntry | None = None
    opsgenie: EffectiveIntegrationEntry | None = None
    prefect: EffectiveIntegrationEntry | None = None
    kafka: EffectiveIntegrationEntry | None = None
    clickhouse: EffectiveIntegrationEntry | None = None
    bitbucket: EffectiveIntegrationEntry | None = None
