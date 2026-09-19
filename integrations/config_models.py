"""Canonical strict models for normalized integration configuration."""

from __future__ import annotations

import re
from base64 import b64encode
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator

from config.constants.azure import (
    AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
    AZURE_MAX_RESULTS_DEFAULT,
    AZURE_MAX_RESULTS_HARD_LIMIT,
)
from config.strict_config import StrictConfigModel
from config.tracer_urls import get_tracer_base_url
from infrastructure.text.url_validation import validate_https_or_loopback_http_url
from integrations._validators import (
    normalize_bearer,
    normalize_bool_str,
    normalize_str,
    normalize_url,
    normalize_with_default,
)

_LOCAL_GRAFANA_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
DEFAULT_DATADOG_SITE = "datadoghq.com"
DEFAULT_CORALOGIX_BASE_URL = "https://api.coralogix.com"
DEFAULT_OPSGENIE_BASE_URLS: dict[str, str] = {
    "us": "https://api.opsgenie.com",
    "eu": "https://api.eu.opsgenie.com",
}
DEFAULT_INCIDENT_IO_BASE_URL = "https://api.incident.io"
DEFAULT_PAGERDUTY_BASE_URL = "https://api.pagerduty.com"


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class GrafanaIntegrationConfig(StrictConfigModel):
    """Normalized Grafana credentials used by resolution and verification flows.

    Grafana supports two authentication styles, and this model is the single
    owner of the "how do I authenticate?" decision so callers (classifier,
    verifier, clients) never re-derive it:

    * **Service account token** — sent as ``Authorization: Bearer <token>``.
      Used by Grafana Cloud and by a locally hosted Grafana that has a token.
    * **Basic auth** — ``username`` + ``password`` sent as
      ``Authorization: Basic <base64>``. Common for local dev Grafana
      (e.g. the default ``admin`` / ``admin`` login).

    A local Grafana with neither is treated as *anonymous* (``is_local`` and
    no credentials): it is reachable without an ``Authorization`` header.
    """

    endpoint: str
    api_key: str = ""
    integration_id: str = ""
    username: str = ""
    password: str = ""
    verify_ssl: bool = True
    ca_bundle: str = ""

    _normalize_endpoint = field_validator("endpoint", mode="before")(normalize_url())
    _normalize_ca_bundle = field_validator("ca_bundle", mode="before")(normalize_str())
    _normalize_verify_ssl = field_validator("verify_ssl", mode="before")(normalize_bool_str())

    @property
    def is_local(self) -> bool:
        host = urlparse(self.endpoint).hostname or ""
        return host in _LOCAL_GRAFANA_HOSTS

    @property
    def ssl_verify(self) -> bool | str:
        """Value to pass as ``requests``' ``verify=`` kwarg.

        A configured CA bundle path takes precedence over the plain
        verify/no-verify toggle, matching :class:`SplunkIntegrationConfig`.
        """
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl

    @property
    def has_token(self) -> bool:
        """Whether a usable service account token is configured.

        The sentinel ``"local"`` is the anonymous-local marker, not a real
        token, so it does not count.
        """
        return bool(self.api_key) and self.api_key != "local"

    @property
    def has_basic_auth(self) -> bool:
        return bool(self.username and self.password)

    @property
    def is_anonymous_local(self) -> bool:
        """A local Grafana reachable without any credentials."""
        return self.is_local and not self.has_token and not self.has_basic_auth

    @property
    def has_usable_credentials(self) -> bool:
        """Whether this config can attempt an authenticated (or anonymous-local) request."""
        return self.has_token or self.has_basic_auth or self.is_anonymous_local

    @property
    def auth_headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header for this config (empty when anonymous)."""
        if self.has_basic_auth:
            token = b64encode(f"{self.username}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {token}"}
        if self.has_token:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}


class DatadogIntegrationConfig(StrictConfigModel):
    """Normalized Datadog credentials used by resolution and verification flows."""

    api_key: str
    app_key: str
    site: str = DEFAULT_DATADOG_SITE
    integration_id: str = ""

    _normalize_site = field_validator("site", mode="before")(
        normalize_with_default(DEFAULT_DATADOG_SITE)
    )

    @property
    def base_url(self) -> str:
        return f"https://api.{self.site}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Content-Type": "application/json",
        }


class CoralogixIntegrationConfig(StrictConfigModel):
    """Normalized Coralogix credentials used by resolution and verification flows."""

    api_key: str
    base_url: str = DEFAULT_CORALOGIX_BASE_URL
    application_name: str = ""
    subsystem_name: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_CORALOGIX_BASE_URL)
    )


# ---------------------------------------------------------------------------
# Cloud / Infrastructure
# ---------------------------------------------------------------------------


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

    _normalize_region = field_validator("region", mode="before")(
        normalize_with_default("us-east-1")
    )

    @model_validator(mode="after")
    def _require_auth_method(self) -> AWSIntegrationConfig:
        if self.role_arn or self.credentials:
            return self
        raise ValueError(
            "AWS integration requires either role_arn or credentials.access_key_id/secret_access_key."
        )


class VercelIntegrationConfig(StrictConfigModel):
    """Normalized Vercel credentials used by resolution and verification flows."""

    api_token: str
    team_id: str = ""
    integration_id: str = ""

    _normalize_api_token = field_validator("api_token", mode="before")(normalize_str())
    _normalize_team_id = field_validator("team_id", mode="before")(normalize_str())

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    @property
    def team_params(self) -> dict[str, str]:
        return {"teamId": self.team_id} if self.team_id else {}


# ---------------------------------------------------------------------------
# Alerting & Incident Management
# ---------------------------------------------------------------------------


class SlackWebhookConfig(StrictConfigModel):
    """Slack webhook runtime config."""

    webhook_url: str

    @model_validator(mode="after")
    def _require_https_slack_url(self) -> SlackWebhookConfig:
        parsed = urlparse(self.webhook_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Slack webhook must be a valid HTTPS URL.")
        hostname = (parsed.hostname or "").lower()
        if hostname != "slack.com" and not hostname.endswith(".slack.com"):
            raise ValueError("Slack webhook host must be a Slack domain.")
        return self


class OpsGenieIntegrationConfig(StrictConfigModel):
    """Normalized OpsGenie credentials used by resolution and verification flows."""

    api_key: str
    region: str = "us"
    integration_id: str = ""

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> str:
        raw = str(value or "us").strip().lower()
        return raw if raw in DEFAULT_OPSGENIE_BASE_URLS else "us"

    @property
    def base_url(self) -> str:
        return DEFAULT_OPSGENIE_BASE_URLS.get(self.region, DEFAULT_OPSGENIE_BASE_URLS["us"])

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"GenieKey {self.api_key}",
            "Content-Type": "application/json",
        }


class PagerDutyIntegrationConfig(StrictConfigModel):
    """PagerDuty config"""

    api_key: str
    base_url: str = DEFAULT_PAGERDUTY_BASE_URL
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(
        normalize_url(DEFAULT_PAGERDUTY_BASE_URL)
    )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token token={self.api_key}",
            "Content-Type": "application/json",
        }


class IncidentIoIntegrationConfig(StrictConfigModel):
    """Normalized incident.io credentials used by investigation and verification flows."""

    api_key: str
    base_url: str = DEFAULT_INCIDENT_IO_BASE_URL
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = normalize_url(DEFAULT_INCIDENT_IO_BASE_URL)(value)
        return validate_https_or_loopback_http_url(normalized, service_name="incident.io")

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, value: object) -> str:
        return normalize_str()(value)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


class AlertmanagerIntegrationConfig(StrictConfigModel):
    """Normalized Alertmanager credentials used by resolution and verification flows."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_strs = field_validator("bearer_token", "username", "password", mode="before")(
        normalize_str()
    )

    @model_validator(mode="after")
    def _no_dual_auth(self) -> AlertmanagerIntegrationConfig:
        if self.bearer_token and self.username:
            raise ValueError(
                "Alertmanager config has both bearer_token and username set; "
                "use one auth method only."
            )
        return self

    @property
    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    @property
    def basic_auth(self) -> tuple[str, str] | None:
        if self.username and self.password:
            return (self.username, self.password)
        return None


class SplunkIntegrationConfig(StrictConfigModel):
    """Normalized Splunk credentials used by resolution and verification flows."""

    base_url: str
    token: str = ""
    index: str = "main"
    verify_ssl: bool = True
    ca_bundle: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_token = field_validator("token", mode="before")(normalize_str())
    _normalize_index = field_validator("index", mode="before")(normalize_with_default("main"))
    _normalize_ca_bundle = field_validator("ca_bundle", mode="before")(normalize_str())

    @property
    def ssl_verify(self) -> bool | str:
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.token)


class VictoriaLogsIntegrationConfig(StrictConfigModel):
    """Normalized VictoriaLogs credentials used by resolution and verification flows."""

    base_url: str
    tenant_id: str | None = None
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_integration_id = field_validator("integration_id", mode="before")(normalize_str())

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _normalize_tenant_id(cls, value: object) -> str | None:
        # Treat empty / blank / None uniformly as "not configured" so the
        # AccountID header is only sent when the user explicitly opts in.
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url)


# ---------------------------------------------------------------------------
# Source Control & CI/CD
# ---------------------------------------------------------------------------


class ArgoCDIntegrationConfig(StrictConfigModel):
    """Normalized Argo CD credentials used by resolution and verification flows."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    project: str = ""
    app_namespace: str = ""
    verify_ssl: bool = True
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or "").strip().rstrip("/")
        return validate_https_or_loopback_http_url(normalized, service_name="Argo CD")

    _normalize_bearer_token = field_validator("bearer_token", mode="before")(normalize_bearer())
    _normalize_strs = field_validator(
        "username", "password", "project", "app_namespace", "integration_id", mode="before"
    )(normalize_str())
    _normalize_verify_ssl = field_validator("verify_ssl", mode="before")(normalize_bool_str())

    @model_validator(mode="after")
    def _no_dual_auth(self) -> ArgoCDIntegrationConfig:
        if self.bearer_token and (self.username or self.password):
            raise ValueError(
                "Argo CD config has both bearer_token and username/password set; "
                "use one auth method only."
            )
        return self

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and (self.bearer_token or (self.username and self.password)))


class HelmIntegrationConfig(StrictConfigModel):
    """Normalized Helm CLI settings for read-only Kubernetes release inspection."""

    helm_path: str = "helm"
    kube_context: str = ""
    kubeconfig: str = ""
    default_namespace: str = ""
    integration_id: str = ""

    _normalize_helm_path = field_validator("helm_path", mode="before")(
        normalize_with_default("helm")
    )
    _normalize_strs = field_validator(
        "kube_context", "kubeconfig", "default_namespace", "integration_id", mode="before"
    )(normalize_str())

    @property
    def is_configured(self) -> bool:
        return bool(str(self.helm_path or "").strip())


class RailwayIntegrationConfig(StrictConfigModel):
    token: str = ""
    railway_path: str = "railway"
    project: str = ""
    service: str = ""
    environment: str = ""
    integration_id: str = ""

    _normalize_railway_path = field_validator("railway_path", mode="before")(
        normalize_with_default("railway")
    )
    _normalize_strs = field_validator(
        "token", "project", "service", "environment", "integration_id", mode="before"
    )(normalize_str())

    @property
    def has_default_scope(self) -> bool:
        return bool(self.project and self.service and self.environment)


# ---------------------------------------------------------------------------
# Databases — Relational
# ---------------------------------------------------------------------------


class PostgreSQLIntegrationConfig(StrictConfigModel):
    """Normalized PostgreSQL credentials used by resolution and verification flows."""

    host: str
    port: int = 5432
    database: str
    username: str = "postgres"
    password: str = ""
    ssl_mode: str = "prefer"
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_database = field_validator("database", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(
        normalize_with_default("postgres")
    )
    _normalize_ssl_mode = field_validator("ssl_mode", mode="before")(
        normalize_with_default("prefer")
    )


class MySQLIntegrationConfig(StrictConfigModel):
    """Normalized MySQL credentials used by resolution and verification flows."""

    host: str
    port: int = 3306
    database: str
    username: str = "root"
    password: str = ""
    ssl_mode: str = "preferred"
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_database = field_validator("database", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(normalize_with_default("root"))
    _normalize_ssl_mode = field_validator("ssl_mode", mode="before")(
        normalize_with_default("preferred")
    )


# ---------------------------------------------------------------------------
# Databases — Document / NoSQL
# ---------------------------------------------------------------------------


class MongoDBIntegrationConfig(StrictConfigModel):
    """Normalized MongoDB credentials used by resolution and verification flows."""

    connection_string: str
    database: str = ""
    auth_source: str = "admin"
    tls: bool = True
    integration_id: str = ""

    _normalize_connection_string = field_validator("connection_string", mode="before")(
        normalize_str()
    )
    _normalize_auth_source = field_validator("auth_source", mode="before")(
        normalize_with_default("admin")
    )


class RedisIntegrationConfig(StrictConfigModel):
    """Normalized Redis credentials used by resolution and verification flows."""

    host: str
    port: int = 6379
    username: str = ""
    password: str = ""
    db: int = 0
    ssl: bool = False
    integration_id: str = ""

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_username = field_validator("username", mode="before")(normalize_str())
    _normalize_password = field_validator("password", mode="before")(normalize_str())


# ---------------------------------------------------------------------------
# Logging & Telemetry
# ---------------------------------------------------------------------------


class BetterStackIntegrationConfig(StrictConfigModel):
    """Normalized Better Stack Telemetry SQL Query API credentials."""

    query_endpoint: str
    username: str
    password: str = ""
    sources: list[str] = []
    integration_id: str = ""

    _normalize_endpoint = field_validator("query_endpoint", mode="before")(normalize_url())
    _normalize_username = field_validator("username", mode="before")(normalize_str())

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


# ---------------------------------------------------------------------------
# Productivity & Collaboration
# ---------------------------------------------------------------------------


class JiraIntegrationConfig(StrictConfigModel):
    """Normalized Jira credentials used by resolution and verification flows."""

    base_url: str
    email: str
    api_token: str
    project_key: str
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_strs = field_validator("email", "api_token", "project_key", mode="before")(
        normalize_str()
    )

    @property
    def auth(self) -> tuple[str, str]:
        return (self.email, self.api_token)

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/rest/api/3"


class ServiceNowIntegrationConfig(StrictConfigModel):
    """Normalized ServiceNow credentials used by resolution and verification flows."""

    instance_url: str
    username: str
    password: str
    integration_id: str = ""

    @field_validator("instance_url", mode="before")
    @classmethod
    def _normalize_instance_url(cls, value: object) -> str:
        normalized = normalize_url()(value)
        return validate_https_or_loopback_http_url(
            normalized, service_name="servicenow", field_name="instance_url"
        )

    _normalize_strs = field_validator("username", "password", "integration_id", mode="before")(
        normalize_str()
    )

    @property
    def auth(self) -> tuple[str, str]:
        return (self.username, self.password)

    @property
    def api_base(self) -> str:
        return f"{self.instance_url}/api/now"


class GoogleDocsIntegrationConfig(StrictConfigModel):
    """Normalized Google Docs (Drive API) credentials for incident report generation."""

    credentials_file: str
    folder_id: str
    integration_id: str = ""
    timeout_seconds: int = 30

    _normalize_credentials_file = field_validator("credentials_file", mode="before")(
        normalize_str()
    )

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


# ---------------------------------------------------------------------------
# Messaging Bots
# ---------------------------------------------------------------------------


class DiscordBotConfig(StrictConfigModel):
    """Discord runtime config."""

    bot_token: str
    application_id: str = ""
    public_key: str = ""
    default_channel_id: str | None = None
    identity_policy: dict[str, object] | None = Field(
        default=None,
        description="Messaging identity policy for inbound security (MessagingIdentityPolicy shape)",
    )

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped

    @field_validator("public_key", mode="before")
    @classmethod
    def _validate_public_key(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if stripped and not re.fullmatch(r"[0-9a-fA-F]+", stripped):
            raise ValueError("public_key must be a valid hexadecimal string")
        return stripped


class TelegramBotConfig(StrictConfigModel):
    """Telegram Bot runtime config."""

    bot_token: str
    default_chat_id: str | None = None
    identity_policy: dict[str, object] | None = Field(
        default=None,
        description="Messaging identity policy for inbound security (MessagingIdentityPolicy shape)",
    )

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped


class RocketChatConfig(StrictConfigModel):
    """Rocket.Chat runtime config.

    Two delivery modes, either or both may be configured:

    - Personal Access Token: ``server_url`` + ``auth_token`` + ``user_id``
      (REST ``chat.postMessage``, dynamic channel targeting).
    - Incoming webhook: ``webhook_url`` (fixed destination chosen when the
      webhook is created in the Rocket.Chat admin).
    """

    server_url: str = ""
    auth_token: str = ""
    user_id: str = ""
    webhook_url: str = ""
    default_channel: str | None = None

    @field_validator("server_url", mode="before")
    @classmethod
    def _normalize_server_url(cls, value: object) -> str:
        stripped = str(value or "").strip().rstrip("/")
        if stripped and not stripped.startswith(("http://", "https://")):
            raise ValueError("server_url must start with http:// or https://")
        return stripped

    @field_validator("auth_token", "user_id", mode="before")
    @classmethod
    def _strip_credential(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _normalize_webhook_url(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            return ""
        parsed = urlparse(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("webhook_url must be a valid HTTP(S) URL")
        return stripped

    @model_validator(mode="after")
    def _require_pat_or_webhook(self) -> RocketChatConfig:
        has_pat = bool(self.server_url and self.auth_token and self.user_id)
        if not has_pat and not self.webhook_url:
            raise ValueError(
                "Rocket.Chat needs either webhook_url or all of server_url, auth_token, and user_id"
            )
        return self


class BuzzConfig(StrictConfigModel):
    """Buzz (Nostr-based workspace) runtime config for delivery via ``buzz-cli``.

    ``private_key`` is the agent's Nostr identity (hex or ``nsec1...``) and is
    the only required field. ``default_channel`` is a Buzz channel UUID —
    channels are identified by UUID, not by name.
    """

    relay_url: str = "http://localhost:3000"
    private_key: str = ""
    default_channel: str = ""
    auth_tag: str = ""
    buzz_path: str = "buzz"
    integration_id: str = ""

    @field_validator("relay_url", mode="before")
    @classmethod
    def _normalize_relay_url(cls, value: object) -> str:
        stripped = str(value or "").strip().rstrip("/")
        if not stripped:
            return "http://localhost:3000"
        if not stripped.startswith(("http://", "https://")):
            raise ValueError("relay_url must start with http:// or https://")
        return stripped

    @field_validator("private_key", "auth_tag", mode="before")
    @classmethod
    def _strip_secret(cls, value: object) -> str:
        return str(value or "").strip()

    _normalize_default_channel = field_validator("default_channel", mode="before")(normalize_str())
    _normalize_buzz_path = field_validator("buzz_path", mode="before")(
        normalize_with_default("buzz")
    )
    _normalize_integration_id = field_validator("integration_id", mode="before")(normalize_str())

    @property
    def is_configured(self) -> bool:
        return bool(self.private_key)


class WhatsAppConfig(StrictConfigModel):
    """Twilio WhatsApp runtime config.

    WhatsApp delivery is owned entirely by the standalone ``whatsapp``
    integration. The unified :class:`TwilioIntegrationConfig` adds SMS as a
    separate channel and intentionally does NOT duplicate WhatsApp.
    """

    account_sid: str
    auth_token: str
    from_number: str
    default_to: str | None = None
    identity_policy: dict[str, object] | None = Field(
        default=None,
        description="Messaging identity policy for inbound security (MessagingIdentityPolicy shape)",
    )

    @field_validator("account_sid", mode="before")
    @classmethod
    def _validate_account_sid(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("account_sid cannot be empty or just whitespace")
        return stripped

    @field_validator("auth_token", mode="before")
    @classmethod
    def _validate_auth_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("auth_token cannot be empty or just whitespace")
        return stripped

    @field_validator("from_number", mode="before")
    @classmethod
    def _validate_from_number(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("from_number cannot be empty or just whitespace")
        return stripped


class TwilioSMSChannelConfig(StrictConfigModel):
    """SMS channel sub-config inside a unified Twilio integration.

    Either ``from_number`` (a Twilio-provisioned phone number) OR
    ``messaging_service_sid`` (a Twilio Messaging Service) must be set
    for the channel to be considered configured.
    """

    enabled: bool = False
    from_number: str = ""
    default_to: str | None = None
    messaging_service_sid: str = ""

    _normalize_strs = field_validator("from_number", "messaging_service_sid", mode="before")(
        normalize_str()
    )
    _normalize_enabled = field_validator("enabled", mode="before")(normalize_bool_str())

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and (self.from_number or self.messaging_service_sid))


class TwilioIntegrationConfig(StrictConfigModel):
    """Unified Twilio runtime config.

    Adds SMS as a Twilio-backed outbound channel. WhatsApp is owned by the
    standalone ``whatsapp`` integration and is intentionally not duplicated
    here. Both can share the same Twilio account credentials.
    """

    account_sid: str
    auth_token: str
    sms: TwilioSMSChannelConfig = Field(default_factory=TwilioSMSChannelConfig)
    integration_id: str = ""

    @field_validator("account_sid", mode="before")
    @classmethod
    def _validate_account_sid(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("account_sid cannot be empty or just whitespace")
        return stripped

    @field_validator("auth_token", mode="before")
    @classmethod
    def _validate_auth_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("auth_token cannot be empty or just whitespace")
        return stripped

    @model_validator(mode="after")
    def _require_sms_channel(self) -> TwilioIntegrationConfig:
        if not self.sms.is_configured:
            raise ValueError(
                "Twilio integration requires the SMS channel configured "
                "(enabled=true with a from_number or messaging_service_sid)."
            )
        return self

    @property
    def configured_channels(self) -> list[str]:
        return ["sms"] if self.sms.is_configured else []


class SlackBotConfig(StrictConfigModel):
    """Slack Bot runtime config for inbound messaging (Socket Mode or Events API).

    Socket Mode uses ``bot_token`` + ``app_token``. Events API HTTP also needs
    ``signing_secret`` — leave it empty for Socket Mode-only installs.
    """

    bot_token: str
    app_token: str = Field(
        default="",
        description="App-level token (xapp-…) for Socket Mode. Required for gateway chat.",
    )
    signing_secret: str = Field(
        default="",
        description="Slack signing secret for webhook HMAC verification. MUST be set for Events API HTTP.",
    )
    app_id: str = ""
    default_chat_id: str = Field(
        default="",
        description="Default channel ID (C…) for scheduled outbound delivery.",
    )
    identity_policy: dict[str, object] | None = Field(
        default=None,
        description="Messaging identity policy for inbound security (MessagingIdentityPolicy shape)",
    )

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: object) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("bot_token cannot be empty or just whitespace")
        return stripped


class SMTPIntegrationConfig(StrictConfigModel):
    """SMTP runtime config for RCA email delivery."""

    host: str
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password: str = ""
    from_address: str
    default_to: str | None = None

    _normalize_host = field_validator("host", mode="before")(normalize_str())
    _normalize_security = field_validator("security", mode="before")(
        normalize_with_default("starttls")
    )
    _normalize_username = field_validator("username", mode="before")(normalize_str())
    _normalize_password = field_validator("password", mode="before")(normalize_str())
    _normalize_from_address = field_validator("from_address", mode="before")(normalize_str())
    _normalize_default_to = field_validator("default_to", mode="before")(normalize_str())

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: object) -> int:
        if isinstance(value, int):
            port = value
        elif isinstance(value, str):
            stripped = value.strip()
            port = int(stripped) if stripped else 587
        else:
            port = 587
        if port <= 0 or port > 65535:
            raise ValueError("port must be between 1 and 65535")
        return port

    @field_validator("security", mode="after")
    @classmethod
    def _validate_security(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"starttls", "ssl", "none"}:
            raise ValueError("security must be one of: starttls, ssl, none")
        return normalized

    @model_validator(mode="after")
    def _validate_auth_pair(self) -> SMTPIntegrationConfig:
        if bool(self.username) != bool(self.password):
            raise ValueError("username and password must both be set, or both be empty")
        if "@" not in self.from_address:
            raise ValueError("from_address must look like an email address")
        if self.default_to and "@" not in self.default_to:
            raise ValueError("default_to must look like an email address")
        return self


# ---------------------------------------------------------------------------
# Cloud Observability Platforms
# ---------------------------------------------------------------------------


class SnowflakeIntegrationConfig(StrictConfigModel):
    """Normalized Snowflake credentials used by resolution and tool flows."""

    account_identifier: str
    token: str
    user: str = ""
    password: str = ""
    warehouse: str = ""
    role: str = ""
    database: str = ""
    db_schema: str = Field(
        default="",
        alias="schema",
        validation_alias=AliasChoices("schema", "db_schema"),
    )
    max_results: int = 50
    integration_id: str = ""

    _normalize_strs = field_validator(
        "user",
        "password",
        "warehouse",
        "role",
        "database",
        "db_schema",
        "integration_id",
        mode="before",
    )(normalize_str())

    @field_validator("max_results", mode="before")
    @classmethod
    def _clamp_max_results(cls, value: object) -> int:
        try:
            v: int = int(value)  # type: ignore[arg-type,call-overload]
        except (TypeError, ValueError):
            return 50
        return max(1, min(v, 200))


class AzureIntegrationConfig(StrictConfigModel):
    """Normalized Azure Monitor Log Analytics credentials."""

    workspace_id: str
    access_token: str
    endpoint: str = AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT
    tenant_id: str = ""
    subscription_id: str = ""
    max_results: int = AZURE_MAX_RESULTS_DEFAULT
    integration_id: str = ""

    _normalize_endpoint = field_validator("endpoint", mode="before")(
        normalize_url(AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT)
    )
    _normalize_strs = field_validator(
        "tenant_id", "subscription_id", "integration_id", mode="before"
    )(normalize_str())

    @field_validator("max_results", mode="before")
    @classmethod
    def _clamp_max_results(cls, value: object) -> int:
        try:
            v: int = int(value)  # type: ignore[arg-type,call-overload]
        except (TypeError, ValueError):
            return AZURE_MAX_RESULTS_DEFAULT
        return max(1, min(v, AZURE_MAX_RESULTS_HARD_LIMIT))


class ObserveIntegrationConfig(StrictConfigModel):
    """Normalized Observe (observe.inc) credentials used by resolution and tool flows."""

    customer_id: str
    api_token: str
    base_url: str = ""
    integration_id: str = ""

    _normalize_strs = field_validator(
        "customer_id", "api_token", "base_url", "integration_id", mode="before"
    )(normalize_str())


class OpenObserveIntegrationConfig(StrictConfigModel):
    """Normalized OpenObserve credentials used by resolution and tool flows."""

    base_url: str
    org: str = "default"
    api_token: str = ""
    username: str = ""
    password: str = ""
    stream: str = ""
    max_results: int = 100
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url())
    _normalize_org = field_validator("org", mode="before")(normalize_with_default("default"))
    _normalize_strs = field_validator(
        "api_token", "username", "password", "stream", "integration_id", mode="before"
    )(normalize_str())

    @field_validator("max_results", mode="before")
    @classmethod
    def _clamp_max_results(cls, value: object) -> int:
        try:
            v: int = int(value)  # type: ignore[arg-type,call-overload]
        except (TypeError, ValueError):
            return 100
        return max(1, min(v, 500))


class OpenSearchIntegrationConfig(StrictConfigModel):
    """Normalized OpenSearch credentials used by resolution and tool flows."""

    url: str
    api_key: str = ""
    username: str = ""
    password: str = ""
    index_pattern: str = "*"
    max_results: int = 100
    integration_id: str = ""

    _normalize_url = field_validator("url", mode="before")(normalize_url())
    _normalize_index_pattern = field_validator("index_pattern", mode="before")(
        normalize_with_default("*")
    )
    _normalize_strs = field_validator(
        "api_key", "username", "password", "integration_id", mode="before"
    )(normalize_str())

    @field_validator("max_results", mode="before")
    @classmethod
    def _clamp_max_results(cls, value: object) -> int:
        try:
            v: int = int(value)  # type: ignore[arg-type,call-overload]
        except (TypeError, ValueError):
            return 100
        return max(1, min(v, 500))


# ---------------------------------------------------------------------------
# Tracer internal
# ---------------------------------------------------------------------------


class TracerIntegrationConfig(StrictConfigModel):
    """Tracer API access config."""

    base_url: str = Field(default_factory=get_tracer_base_url)
    jwt_token: str

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        return str(value or get_tracer_base_url()).strip() or get_tracer_base_url()

    _normalize_token = field_validator("jwt_token", mode="before")(normalize_bearer())


# ---------------------------------------------------------------------------
# SaaS / workflow integrations (config-only, no active verifier)
# ---------------------------------------------------------------------------


class PrefectIntegrationConfig(StrictConfigModel):
    api_url: str = "https://api.prefect.cloud/api"
    api_key: str = ""
    account_id: str = ""
    workspace_id: str = ""
    integration_id: str = ""

    _normalize_api_url = field_validator("api_url", mode="before")(
        normalize_url("https://api.prefect.cloud/api")
    )
    _normalize_strs = field_validator("api_key", "account_id", "workspace_id", mode="before")(
        normalize_str()
    )


class TemporalIntegrationConfig(StrictConfigModel):
    base_url: str = ""
    namespace: str = "default"
    api_key: str = ""
    integration_id: str = ""

    _normalize_base_url = field_validator("base_url", mode="before")(normalize_url(""))
    _normalize_strs = field_validator("api_key", "namespace", mode="before")(normalize_str())

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        return headers


class KubernetesIntegrationConfig(StrictConfigModel):
    """Normalized Kubernetes credentials used by resolution and verification flows.

    Supports two mutually compatible auth paths:
    - ``kubeconfig_path``: path to a kubeconfig file on disk (preferred when
      available — ``load_kube_config`` handles single-file and multi-file merged
      configs via the standard KUBECONFIG env var semantics).
    - ``kubeconfig``: raw kubeconfig YAML string stored inline (used when the
      config is embedded, e.g. from a secrets manager or stored integration).

    ``kubeconfig_path`` takes precedence over ``kubeconfig`` at connection time.
    """

    kubeconfig: str = ""
    kubeconfig_path: str = ""
    context: str = ""
    namespace: str = "default"
    integration_id: str = ""

    _normalize_strs = field_validator(
        "kubeconfig_path", "context", "integration_id", mode="before"
    )(normalize_str())
    _normalize_namespace = field_validator("namespace", mode="before")(
        normalize_with_default("default")
    )

    @property
    def is_configured(self) -> bool:
        return bool(self.kubeconfig or self.kubeconfig_path)
