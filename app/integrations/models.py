"""Shared strict models for normalized integration configuration."""

from __future__ import annotations

import re
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


class PostgreSQLIntegrationConfig(StrictConfigModel):
    """Normalized PostgreSQL credentials used by resolution and verification flows."""

    host: str
    port: int = 5432
    database: str
    username: str = "postgres"
    password: str = ""
    ssl_mode: str = "prefer"
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before")
    @classmethod
    def _normalize_database(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: object) -> str:
        normalized = str(value or "postgres").strip()
        return normalized or "postgres"

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _normalize_ssl_mode(cls, value: object) -> str:
        normalized = str(value or "prefer").strip()
        return normalized or "prefer"


class AzureSQLIntegrationConfig(StrictConfigModel):
    """Normalized Azure SQL Database credentials used by resolution and verification flows."""

    server: str
    port: int = 1433
    database: str
    username: str = ""
    password: str = ""
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    integration_id: str = ""

    @field_validator("server", "database", "username", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("driver", mode="before")
    @classmethod
    def _normalize_driver(cls, value: object) -> str:
        normalized = str(value or "ODBC Driver 18 for SQL Server").strip()
        return normalized or "ODBC Driver 18 for SQL Server"


class MySQLIntegrationConfig(StrictConfigModel):
    """Normalized MySQL credentials used by resolution and verification flows."""

    host: str
    port: int = 3306
    database: str
    username: str = "root"
    password: str = ""
    ssl_mode: str = "preferred"
    integration_id: str = ""

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("database", mode="before")
    @classmethod
    def _normalize_database(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: object) -> str:
        normalized = str(value or "root").strip()
        return normalized or "root"

    @field_validator("ssl_mode", mode="before")
    @classmethod
    def _normalize_ssl_mode(cls, value: object) -> str:
        normalized = str(value or "preferred").strip()
        return normalized or "preferred"


class MariaDBIntegrationConfig(StrictConfigModel):
    """Normalized MariaDB credentials used by resolution and verification flows."""

    host: str
    port: int = 3306
    database: str
    username: str
    password: str = ""
    ssl: bool = True
    integration_id: str = ""

    @field_validator("host", "database", "username", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()


class RabbitMQIntegrationConfig(StrictConfigModel):
    """Normalized RabbitMQ Management API credentials used by resolution and verification flows."""

    host: str
    management_port: int = 15672
    username: str
    password: str = ""
    vhost: str = "/"
    ssl: bool = False
    verify_ssl: bool = True
    integration_id: str = ""

    @field_validator("host", "username", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("vhost", mode="before")
    @classmethod
    def _normalize_vhost(cls, value: object) -> str:
        raw = str(value or "").strip()
        return raw or "/"


class BetterStackIntegrationConfig(StrictConfigModel):
    """Normalized Better Stack Telemetry SQL Query API credentials used by resolution and verification flows."""

    query_endpoint: str
    username: str
    password: str = ""
    sources: list[str] = []
    integration_id: str = ""

    @field_validator("query_endpoint", mode="before")
    @classmethod
    def _normalize_endpoint(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: object) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []


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
        if isinstance(value, str):
            try:
                timeout = int(value)
            except ValueError:
                return 30
        elif isinstance(value, int | float):
            timeout = int(value)
        else:
            return 30
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


class NotionIntegrationConfig(StrictConfigModel):
    """Normalized Notion credentials used by resolution and verification flows."""

    api_key: str
    database_id: str
    integration_id: str = ""

    @field_validator("api_key", "database_id", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()


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


class DiscordBotConfig(StrictConfigModel):
    """Discord runtime config."""

    bot_token: str  # Bot token for API calls
    application_id: str = ""  # For slash command registration (required for inbound only)
    public_key: str = ""  # For signature verification (required for inbound only)
    default_channel_id: str | None = None  # Fallback for CLI-triggered findings

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, v: object) -> str:
        stripped = str(v or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped

    @field_validator("public_key", mode="before")
    @classmethod
    def _validate_public_key(cls, v: object) -> str:
        stripped = str(v or "").strip()
        if not stripped:
            return stripped  # optional — only needed for inbound interactions endpoint
        if not re.fullmatch(r"[0-9a-fA-F]+", stripped):
            raise ValueError("public_key must be a valid hexadecimal string")
        return stripped


class AlertmanagerIntegrationConfig(StrictConfigModel):
    """Normalized Alertmanager credentials used by resolution and verification flows."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("bearer_token", "username", "password", mode="before")
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _no_dual_auth(self) -> AlertmanagerIntegrationConfig:
        if self.bearer_token and self.username:
            raise ValueError(
                "Alertmanager config has both bearer_token and username set; "
                "use one auth method only."
            )
        return self


class IntegrationInstance(StrictConfigModel):
    """One named instance of a provider.

    A single integration record (e.g. a ``grafana`` entry in the store) can
    carry multiple named instances — for example, a ``prod`` and a
    ``staging`` Grafana cluster. The name is normalized to lowercase and
    tag keys are constrained to ``^[a-z][a-z0-9_-]*$``.
    """

    name: str = "default"
    tags: dict[str, str] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        text = str(value or "default").strip().lower()
        return text or "default"

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for k, v in value.items():
            key = str(k).strip().lower()
            val = str(v).strip().lower()
            if key and val and re.match(r"^[a-z][a-z0-9_-]*$", key):
                normalized[key] = val
        return normalized


class EffectiveIntegrationEntry(StrictConfigModel):
    """Resolved integration entry with source metadata.

    ``config`` is the flat default-instance view (backward compat). When
    multiple instances are configured for the service, ``instances`` carries
    them as ``[{name, tags, config, integration_id}, ...]`` — a pass-through
    JSON shape (not the strict ``IntegrationInstance`` model) so the
    catalog layer can enrich each with the already-classified flat config
    without re-validating through Pydantic's forbidding config.
    """

    source: str
    config: dict[str, Any]
    instances: list[dict[str, Any]] | None = None


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
    mariadb: EffectiveIntegrationEntry | None = None
    rabbitmq: EffectiveIntegrationEntry | None = None
    betterstack: EffectiveIntegrationEntry | None = None
    google_docs: EffectiveIntegrationEntry | None = None
    gitlab: EffectiveIntegrationEntry | None = None
    vercel: EffectiveIntegrationEntry | None = None
    jira: EffectiveIntegrationEntry | None = None
    opsgenie: EffectiveIntegrationEntry | None = None
    notion: EffectiveIntegrationEntry | None = None
    prefect: EffectiveIntegrationEntry | None = None
    posthog: EffectiveIntegrationEntry | None = None
    kafka: EffectiveIntegrationEntry | None = None
    clickhouse: EffectiveIntegrationEntry | None = None
    postgresql: EffectiveIntegrationEntry | None = None
    azure_sql: EffectiveIntegrationEntry | None = None
    bitbucket: EffectiveIntegrationEntry | None = None
    trello: EffectiveIntegrationEntry | None = None
    discord: EffectiveIntegrationEntry | None = None
    openclaw: EffectiveIntegrationEntry | None = None
    mysql: EffectiveIntegrationEntry | None = None
    snowflake: EffectiveIntegrationEntry | None = None
    azure: EffectiveIntegrationEntry | None = None
    openobserve: EffectiveIntegrationEntry | None = None
    opensearch: EffectiveIntegrationEntry | None = None
    alertmanager: EffectiveIntegrationEntry | None = None
