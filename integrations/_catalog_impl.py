"""Shared integration catalog for normalization and resolution."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from config.constants.alertmanager import (
    ALERTMANAGER_BEARER_TOKEN_ENV,
    ALERTMANAGER_PASSWORD_ENV,
    ALERTMANAGER_URL_ENV,
    ALERTMANAGER_USERNAME_ENV,
)
from config.constants.aws import (
    AWS_ACCESS_KEY_ID_ENV,
    AWS_EXTERNAL_ID_ENV,
    AWS_REGION_ENV,
    AWS_ROLE_ARN_ENV,
    AWS_SECRET_ACCESS_KEY_ENV,
    AWS_SESSION_TOKEN_ENV,
)
from config.constants.azure import (
    AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
    AZURE_LOG_ANALYTICS_ENDPOINT_ENV,
    AZURE_LOG_ANALYTICS_TOKEN_ENV,
    AZURE_LOG_ANALYTICS_WORKSPACE_ID_ENV,
    AZURE_MAX_RESULTS_DEFAULT,
    AZURE_MAX_RESULTS_ENV,
    AZURE_SUBSCRIPTION_ID_ENV,
    AZURE_TENANT_ID_ENV,
)
from config.constants.azure_sql import (
    AZURE_SQL_DATABASE_ENV,
    AZURE_SQL_DRIVER_ENV,
    AZURE_SQL_ENCRYPT_ENV,
    AZURE_SQL_PASSWORD_ENV,
    AZURE_SQL_PORT_ENV,
    AZURE_SQL_SERVER_ENV,
    AZURE_SQL_USERNAME_ENV,
)
from config.constants.betterstack import (
    BETTERSTACK_PASSWORD_ENV,
    BETTERSTACK_QUERY_ENDPOINT_ENV,
    BETTERSTACK_SOURCES_ENV,
    BETTERSTACK_USERNAME_ENV,
)
from config.constants.buzz import (
    BUZZ_AUTH_TAG_ENV,
    BUZZ_DEFAULT_CHANNEL_ENV,
    BUZZ_PATH_ENV,
    BUZZ_PRIVATE_KEY_ENV,
    BUZZ_RELAY_URL_ENV,
)
from config.constants.coralogix import (
    CORALOGIX_API_KEY_ENV,
    CORALOGIX_APPLICATION_NAME_ENV,
    CORALOGIX_BASE_URL_ENV,
    CORALOGIX_SUBSYSTEM_NAME_ENV,
)
from config.constants.dagster import DAGSTER_API_TOKEN_ENV, DAGSTER_ENDPOINT_ENV
from config.constants.datadog import (
    DATADOG_API_KEY_ENV,
    DATADOG_APP_KEY_ENV,
    DATADOG_SITE_ENV,
)
from config.constants.discord import (
    DISCORD_APPLICATION_ID_ENV,
    DISCORD_BOT_TOKEN_ENV,
    DISCORD_DEFAULT_CHANNEL_ID_ENV,
    DISCORD_PUBLIC_KEY_ENV,
)
from config.constants.github import (
    GITHUB_MCP_ARGS_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_MCP_COMMAND_ENV,
    GITHUB_MCP_MODE_ENV,
    GITHUB_MCP_TOOLSETS_ENV,
    GITHUB_MCP_URL_ENV,
)
from config.constants.gitlab import GITLAB_AUTH_TOKEN_ENV, GITLAB_BASE_URL_ENV
from config.constants.google_docs import (
    GOOGLE_CREDENTIALS_FILE_ENV,
    GOOGLE_DRIVE_FOLDER_ID_ENV,
)
from config.constants.grafana import (
    GRAFANA_CA_BUNDLE_ENV,
    GRAFANA_INSTANCE_URL_ENV,
    GRAFANA_READ_TOKEN_ENV,
    GRAFANA_VERIFY_SSL_ENV,
)
from config.constants.groundcover import (
    GROUNDCOVER_API_KEY_ENV,
    GROUNDCOVER_BACKEND_ID_ENV,
    GROUNDCOVER_MCP_TOKEN_ENV,
    GROUNDCOVER_MCP_URL_ENV,
    GROUNDCOVER_TENANT_UUID_ENV,
    GROUNDCOVER_TIMEZONE_ENV,
)
from config.constants.helm import (
    HELM_KUBE_CONTEXT_ENV,
    HELM_KUBECONFIG_ENV,
    HELM_NAMESPACE_ENV,
    HELM_PATH_ENV,
    OSRE_HELM_INTEGRATION_ENV,
)
from config.constants.honeycomb import (
    HONEYCOMB_API_KEY_ENV,
    HONEYCOMB_BASE_URL_ENV,
    HONEYCOMB_DATASET_ENV,
)
from config.constants.incident_io import INCIDENT_IO_API_KEY_ENV, INCIDENT_IO_BASE_URL_ENV
from config.constants.kubernetes import (
    KUBECONFIG_CONTENT_ENV,
    KUBECONFIG_CONTEXT_ENV,
    KUBECONFIG_NAMESPACE_ENV,
    KUBECONFIG_PATH_ENV,
)
from config.constants.mariadb import (
    MARIADB_DATABASE_ENV,
    MARIADB_HOST_ENV,
    MARIADB_PASSWORD_ENV,
    MARIADB_PORT_ENV,
    MARIADB_SSL_ENV,
    MARIADB_USERNAME_ENV,
)
from config.constants.mongodb import (
    MONGODB_AUTH_SOURCE_ENV,
    MONGODB_CONNECTION_STRING_ENV,
    MONGODB_DATABASE_ENV,
    MONGODB_TLS_ENV,
)
from config.constants.mongodb_atlas import (
    MONGODB_ATLAS_BASE_URL_ENV,
    MONGODB_ATLAS_PRIVATE_KEY_ENV,
    MONGODB_ATLAS_PROJECT_ID_ENV,
    MONGODB_ATLAS_PUBLIC_KEY_ENV,
)
from config.constants.mysql import (
    MYSQL_DATABASE_ENV,
    MYSQL_HOST_ENV,
    MYSQL_PASSWORD_ENV,
    MYSQL_PORT_ENV,
    MYSQL_SSL_MODE_ENV,
    MYSQL_USERNAME_ENV,
)
from config.constants.new_relic import (
    NEW_RELIC_ACCOUNT_ID_ENV,
    NEW_RELIC_API_KEY_ENV,
    NEW_RELIC_BASE_URL_ENV,
    NEW_RELIC_INSTANCES_ENV,
)
from config.constants.opensearch import (
    OPENSEARCH_API_KEY_ENV,
    OPENSEARCH_PASSWORD_ENV,
    OPENSEARCH_URL_ENV,
    OPENSEARCH_USERNAME_ENV,
)
from config.constants.pagerduty import PAGERDUTY_API_KEY_ENV, PAGERDUTY_BASE_URL_ENV
from config.constants.postgresql import (
    POSTGRESQL_DATABASE_ENV,
    POSTGRESQL_HOST_ENV,
    POSTGRESQL_PASSWORD_ENV,
    POSTGRESQL_PORT_ENV,
    POSTGRESQL_SSL_MODE_ENV,
    POSTGRESQL_USERNAME_ENV,
)
from config.constants.posthog_mcp import (
    POSTHOG_MCP_AUTH_TOKEN_ENV,
    POSTHOG_MCP_PROJECT_ID_ENV,
    POSTHOG_MCP_URL_ENV,
)
from config.constants.railway import (
    RAILWAY_ENVIRONMENT_ENV,
    RAILWAY_PATH_ENV,
    RAILWAY_PROJECT_ENV,
    RAILWAY_SERVICE_ENV,
    RAILWAY_TOKEN_ENV,
)
from config.constants.rocketchat import (
    ROCKETCHAT_AUTH_TOKEN_ENV,
    ROCKETCHAT_DEFAULT_CHANNEL_ENV,
    ROCKETCHAT_SERVER_URL_ENV,
    ROCKETCHAT_USER_ID_ENV,
)
from config.constants.sentry import (
    DEFAULT_SENTRY_BASE_URL,
    SENTRY_AUTH_TOKEN_ENV,
    SENTRY_BASE_URL_ENV,
    SENTRY_ORGANIZATION_SLUG_ENV,
    SENTRY_PROJECT_SLUG_ENV,
)
from config.constants.sentry_mcp import (
    SENTRY_MCP_AUTH_TOKEN_ENV,
    SENTRY_MCP_HOST_ENV,
    SENTRY_MCP_URL_ENV,
)
from config.constants.servicenow import (
    SERVICENOW_INSTANCE_URL_ENV,
    SERVICENOW_PASSWORD_ENV,
    SERVICENOW_USERNAME_ENV,
)
from config.constants.slack import (
    SLACK_APP_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_DEFAULT_CHAT_ID_ENV,
    SLACK_WEBHOOK_URL_ENV,
)
from config.constants.smtp import (
    SMTP_DEFAULT_TO_ENV,
    SMTP_FROM_ADDRESS_ENV,
    SMTP_HOST_ENV,
    SMTP_PASSWORD_ENV,
    SMTP_PORT_ENV,
    SMTP_SECURITY_ENV,
    SMTP_USERNAME_ENV,
)
from config.constants.telegram import (
    TELEGRAM_BOT_TOKEN_ENV,
    TELEGRAM_DEFAULT_CHAT_ID_ENV,
)
from config.constants.temporal import (
    TEMPORAL_API_KEY_ENV,
    TEMPORAL_BASE_URL_ENV,
    TEMPORAL_NAMESPACE_ENV,
)
from config.constants.tracer import TRACER_BASE_URL_ENV, TRACER_JWT_TOKEN_ENV
from config.constants.twilio import (
    TWILIO_ACCOUNT_SID_ENV,
    TWILIO_AUTH_TOKEN_ENV,
    TWILIO_SMS_DEFAULT_TO_ENV,
    TWILIO_SMS_FROM_ENV,
    TWILIO_SMS_MESSAGING_SERVICE_SID_ENV,
    TWILIO_WHATSAPP_FROM_ENV,
    WHATSAPP_DEFAULT_TO_ENV,
)
from config.constants.vercel import VERCEL_API_TOKEN_ENV, VERCEL_TEAM_ID_ENV
from config.constants.x_mcp import X_MCP_AUTH_TOKEN_ENV, X_MCP_URL_ENV
from config.constants.yandex_cloud import (
    YC_CLOUD_ID_ENV,
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_TOKEN_ENV,
    YC_USE_METADATA_ENV,
)
from config.llm_credentials import resolve_env_credential
from config.tracer_urls import get_tracer_base_url
from infrastructure.observability.errors.boundary import report_exception
from infrastructure.text.coercion import safe_int
from integrations.airflow.config import airflow_config_from_env
from integrations.airflow.config import classify as _classify_airflow
from integrations.alertmanager import classify as _classify_alertmanager
from integrations.argocd import classify as _classify_argocd
from integrations.aws import classify as _classify_aws
from integrations.azure import classify as _classify_azure
from integrations.azure_sql import build_azure_sql_config
from integrations.azure_sql import classify as _classify_azure_sql
from integrations.betterstack import build_betterstack_config
from integrations.betterstack import classify as _classify_betterstack
from integrations.bitbucket.config import classify as _classify_bitbucket
from integrations.buzz import classify as _classify_buzz
from integrations.config_models import (
    DEFAULT_DATADOG_SITE,
    AlertmanagerIntegrationConfig,
    ArgoCDIntegrationConfig,
    AWSIntegrationConfig,
    BuzzConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    DiscordBotConfig,
    GoogleDocsIntegrationConfig,
    GrafanaIntegrationConfig,
    HelmIntegrationConfig,
    IncidentIoIntegrationConfig,
    JiraIntegrationConfig,
    KubernetesIntegrationConfig,
    OpsGenieIntegrationConfig,
    PagerDutyIntegrationConfig,
    RailwayIntegrationConfig,
    RocketChatConfig,
    ServiceNowIntegrationConfig,
    SlackWebhookConfig,
    SMTPIntegrationConfig,
    SplunkIntegrationConfig,
    TelegramBotConfig,
    TwilioIntegrationConfig,
    VictoriaLogsIntegrationConfig,
    WhatsAppConfig,
)
from integrations.coralogix import classify as _classify_coralogix
from integrations.dagster import build_dagster_config
from integrations.dagster import classify as _classify_dagster
from integrations.datadog import classify as _classify_datadog
from integrations.discord import classify as _classify_discord
from integrations.effective_models import EffectiveIntegrations
from integrations.github.mcp import build_github_mcp_config
from integrations.github.mcp import classify as _classify_github
from integrations.gitlab import DEFAULT_GITLAB_BASE_URL, build_gitlab_config
from integrations.gitlab import classify as _classify_gitlab
from integrations.grafana import classify as _classify_grafana
from integrations.groundcover import classify as _classify_groundcover
from integrations.groundcover.config import GroundcoverIntegrationConfig
from integrations.helm import classify as _classify_helm
from integrations.honeycomb import classify as _classify_honeycomb
from integrations.honeycomb.config import HoneycombIntegrationConfig
from integrations.incident_io import classify as _classify_incident_io
from integrations.jenkins import classify as _classify_jenkins
from integrations.jenkins import jenkins_config_from_env
from integrations.jira import classify as _classify_jira
from integrations.kubernetes import classify as _classify_kubernetes
from integrations.mariadb import build_mariadb_config
from integrations.mariadb import classify as _classify_mariadb
from integrations.mongodb import build_mongodb_config
from integrations.mongodb import classify as _classify_mongodb
from integrations.mongodb_atlas import build_mongodb_atlas_config
from integrations.mongodb_atlas import classify as _classify_mongodb_atlas
from integrations.mysql import build_mysql_config
from integrations.mysql import classify as _classify_mysql
from integrations.new_relic import classify as _classify_new_relic
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.openobserve import classify as _classify_openobserve
from integrations.opensearch import classify as _classify_opensearch
from integrations.opsgenie import classify as _classify_opsgenie
from integrations.pagerduty import classify as _classify_pagerduty
from integrations.postgresql import build_postgresql_config
from integrations.postgresql import classify as _classify_postgresql
from integrations.posthog import posthog_config_from_env
from integrations.posthog.classify import classify as _classify_posthog
from integrations.posthog_mcp import DEFAULT_POSTHOG_MCP_URL, build_posthog_mcp_config
from integrations.posthog_mcp import classify as _classify_posthog_mcp
from integrations.prefect import classify as _classify_prefect
from integrations.rabbitmq import build_rabbitmq_config
from integrations.rabbitmq import classify as _classify_rabbitmq
from integrations.railway import classify as _classify_railway
from integrations.rds import classify as _classify_rds
from integrations.rds import rds_config_from_env
from integrations.redis import classify as _classify_redis
from integrations.redis import redis_config_from_env
from integrations.registry import (
    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES,
    INTEGRATION_SPECS_BY_SERVICE,
    SKIP_CLASSIFIED_SERVICES,
    external_integration_services,
    family_key,
    service_key,
)
from integrations.rocketchat import classify as _classify_rocketchat
from integrations.sentry import build_sentry_config
from integrations.sentry import classify as _classify_sentry
from integrations.sentry_mcp import DEFAULT_SENTRY_MCP_URL, build_sentry_mcp_config
from integrations.sentry_mcp import classify as _classify_sentry_mcp
from integrations.servicenow import classify as _classify_servicenow
from integrations.signoz import classify as _classify_signoz
from integrations.signoz import signoz_config_from_env
from integrations.slack.classify import classify as _classify_slack
from integrations.smtp import classify as _classify_smtp
from integrations.snowflake import classify as _classify_snowflake
from integrations.splunk import classify as _classify_splunk
from integrations.store import _STRUCTURAL_RECORD_FIELDS, load_integrations
from integrations.supabase import build_supabase_config
from integrations.supabase import classify as _classify_supabase
from integrations.telegram import classify as _classify_telegram
from integrations.tempo import classify as _classify_tempo
from integrations.tempo import tempo_config_from_env
from integrations.temporal import classify as _classify_temporal
from integrations.temporal.client import TemporalConfig
from integrations.twilio import classify as _classify_twilio
from integrations.vercel import classify as _classify_vercel
from integrations.vercel.client import VercelConfig
from integrations.victoria_logs import classify as _classify_victoria_logs
from integrations.whatsapp import classify as _classify_whatsapp
from integrations.x_mcp import build_x_mcp_config
from integrations.x_mcp import classify as _classify_x_mcp
from integrations.yandex_cloud import classify as _classify_yandex_cloud
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig

logger = logging.getLogger(__name__)


def _report_env_loader_failure(exc: BaseException, *, integration: str) -> None:
    """Route a per-vendor env-loader failure to Sentry + warning log.

    Replaces ``except Exception: pass`` and ``logger.debug(..., exc_info=True)``
    paths in ``load_env_integrations``: integration is still skipped, but the
    misconfiguration reaches Sentry rather than being lost to debug output
    (#1468).
    """
    report_exception(
        exc,
        logger=logger,
        message=f"env_loader_failed: integration={integration}",
        severity="warning",
        tags={
            "surface": "integration",
            "component": "integrations._catalog_impl",
            "integration": integration,
            "event": "env_loader_failed",
        },
    )


def _should_publish_instance_siblings(instances: object) -> bool:
    """Return whether an effective integration should expose its ``instances`` list."""
    if not isinstance(instances, list) or not instances:
        return False
    if len(instances) > 1:
        return True
    return str(instances[0].get("name", "default")) != "default"


def _record_instances(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a record (v1 or v2 shape) into a list of instance dicts.

    v2 records return their ``instances`` list directly. v1 records are
    migrated on the fly: ``credentials`` plus every non-structural top-level
    field (e.g. AWS ``role_arn``) become the single ``default`` instance's
    credentials. This matches the v1→v2 store migration so downstream
    classification logic reads ONE uniform shape.
    """
    if isinstance(record.get("instances"), list):
        return [inst if isinstance(inst, dict) else {} for inst in record["instances"]]
    credentials = dict(record.get("credentials", {}))
    for key, value in record.items():
        if key in _STRUCTURAL_RECORD_FIELDS or key == "credentials":
            continue
        credentials.setdefault(key, value)
    return [{"name": "default", "tags": {}, "credentials": credentials}]


def classify_integrations(integrations: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify active integrations by service into normalized runtime configs.

    Backward compat: for each ``service``, ``resolved[service]`` is the flat
    config dict of the DEFAULT (first) instance, matching the pre-multi-instance
    contract. When multiple instances exist (or an instance has an explicit
    non-``default`` name), a sibling key ``_all_{service}_instances`` carries
    all of them as ``[{name, tags, config, integration_id}, ...]``. See
    ``integrations/selectors.py`` for consumers.
    """
    resolved: dict[str, Any] = {}
    all_instances: dict[str, list[dict[str, Any]]] = {}

    active = [integration for integration in integrations if integration.get("status") == "active"]

    for integration in active:
        service = str(integration.get("service") or "").strip()
        if not service:
            continue

        service_lower = service.lower()
        if service_lower in SKIP_CLASSIFIED_SERVICES:
            continue

        key = service_key(service_lower)
        record_id = str(integration.get("id", "")).strip()

        for instance in _record_instances(integration):
            credentials = instance.get("credentials", {}) or {}
            instance_name = str(instance.get("name", "default")).strip().lower() or "default"
            instance_tags = instance.get("tags", {}) or {}
            flat_view, flat_key = _classify_service_instance(key, credentials, record_id=record_id)
            if flat_view is None or flat_key is None:
                continue
            resolved.setdefault(flat_key, flat_view)
            # Bucket under the family key so related classifier outputs (e.g.
            # grafana + grafana_local) share one _all_<family>_instances list.
            all_instances.setdefault(family_key(flat_key), []).append(
                {
                    "name": instance_name,
                    "tags": instance_tags,
                    "config": flat_view,
                    "integration_id": record_id,
                }
            )

    for service, instances in all_instances.items():
        if len(instances) > 1 or (instances and instances[0]["name"] != "default"):
            resolved[f"_all_{service}_instances"] = instances

    resolved["_all"] = active
    return resolved


_ClassifyFn = Callable[[dict[str, Any], str], tuple[Any | None, str | None]]


_CLASSIFIERS: dict[str, _ClassifyFn] = {
    "grafana": _classify_grafana,
    "grafana_local": _classify_grafana,
    "aws": _classify_aws,
    "datadog": _classify_datadog,
    "groundcover": _classify_groundcover,
    "honeycomb": _classify_honeycomb,
    "coralogix": _classify_coralogix,
    "github": _classify_github,
    "sentry": _classify_sentry,
    "gitlab": _classify_gitlab,
    "jenkins": _classify_jenkins,
    "mongodb": _classify_mongodb,
    "redis": _classify_redis,
    "postgresql": _classify_postgresql,
    "mongodb_atlas": _classify_mongodb_atlas,
    "mariadb": _classify_mariadb,
    "vercel": _classify_vercel,
    "opsgenie": _classify_opsgenie,
    "pagerduty": _classify_pagerduty,
    "incident_io": _classify_incident_io,
    "jira": _classify_jira,
    "servicenow": _classify_servicenow,
    "discord": _classify_discord,
    "telegram": _classify_telegram,
    "rocketchat": _classify_rocketchat,
    "buzz": _classify_buzz,
    "slack": _classify_slack,
    "whatsapp": _classify_whatsapp,
    "twilio": _classify_twilio,
    "posthog": _classify_posthog,
    "posthog_mcp": _classify_posthog_mcp,
    "sentry_mcp": _classify_sentry_mcp,
    "x_mcp": _classify_x_mcp,
    "mysql": _classify_mysql,
    "dagster": _classify_dagster,
    "rabbitmq": _classify_rabbitmq,
    "rds": _classify_rds,
    "airflow": _classify_airflow,
    "betterstack": _classify_betterstack,
    "azure_sql": _classify_azure_sql,
    "alertmanager": _classify_alertmanager,
    "kubernetes": _classify_kubernetes,
    "argocd": _classify_argocd,
    "helm": _classify_helm,
    "victoria_logs": _classify_victoria_logs,
    "bitbucket": _classify_bitbucket,
    "snowflake": _classify_snowflake,
    "azure": _classify_azure,
    "openobserve": _classify_openobserve,
    "opensearch": _classify_opensearch,
    "splunk": _classify_splunk,
    "supabase": _classify_supabase,
    "signoz": _classify_signoz,
    "tempo": _classify_tempo,
    "temporal": _classify_temporal,
    "smtp": _classify_smtp,
    "prefect": _classify_prefect,
    "railway": _classify_railway,
    "new_relic": _classify_new_relic,
    "yandex_cloud": _classify_yandex_cloud,
}


def _classify_service_instance(
    key: str, credentials: dict[str, Any], *, record_id: str
) -> tuple[Any | None, str | None]:
    """Classify one instance into (flat_view, resolved_key).

    Returns ``(None, None)`` when the instance is invalid or should be skipped
    (e.g. required field missing). The returned ``resolved_key`` is usually
    ``key`` itself, but Grafana splits into ``grafana`` or ``grafana_local``
    based on its ``is_local`` property.
    """
    handler = _CLASSIFIERS.get(key)
    if handler is not None:
        return handler(credentials, record_id)
    # Fallback for unknown services: pass through credentials + record id.
    return {"credentials": credentials, "integration_id": record_id}, key


def _parse_instances_env(env_name: str, service: str) -> dict[str, Any] | None:
    """Parse ``<SERVICE>_INSTANCES`` env var into a v2 integration record.

    Accepts a JSON array of instance entries. Each entry may be either
    ``{"name": ..., "tags": {...}, "credentials": {...}}`` or a flat
    ``{"name": ..., "tags": {...}, <field>: <value>, ...}`` — we accept
    both shapes and normalize to ``credentials``. Returns None if the env
    var is unset, empty, invalid JSON, or not a non-empty list (logs a
    warning on parse failure so callers can fall through to legacy vars).

    Critical: always returns a SINGLE record with multiple instances inside,
    never multiple records — otherwise ``merge_integrations_by_service``
    would drop all but one (PR #527 bug #2).
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Do NOT include exc.msg or the raw value — JSONDecodeError messages
        # embed a slice of the offending input, which could leak a fragment
        # of an API key if the env var was accidentally populated with a
        # credential instead of a JSON array. Log only position + line/col.
        logger.warning(
            "%s is not valid JSON (parse failed at line %d col %d); falling back to legacy vars",
            env_name,
            exc.lineno,
            exc.colno,
        )
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    instances: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        nested_creds = entry.get("credentials")
        if isinstance(nested_creds, dict):
            credentials = dict(nested_creds)
        else:
            credentials = {k: v for k, v in entry.items() if k not in {"name", "tags"}}
        name = str(entry.get("name", "default")).strip().lower() or "default"
        tags = entry.get("tags") if isinstance(entry.get("tags"), dict) else {}
        instances.append({"name": name, "tags": tags, "credentials": credentials})
    if not instances:
        return None
    return {
        "id": f"env-{service}",
        "service": service,
        "status": "active",
        "instances": instances,
    }


def _active_env_record(
    service: str,
    credentials: dict[str, Any],
    *,
    record_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": record_id or f"env-{service.replace('_', '-')}",
        "service": service,
        "status": "active",
        **extra,
        "credentials": credentials,
    }


def load_env_integrations() -> list[dict[str, Any]]:
    """Build integration records from local environment variables."""
    integrations: list[dict[str, Any]] = []

    railway_token = resolve_env_credential(RAILWAY_TOKEN_ENV)
    railway_config = RailwayIntegrationConfig.model_validate(
        {
            "token": railway_token,
            "railway_path": os.getenv(RAILWAY_PATH_ENV, "railway"),
            "project": os.getenv(RAILWAY_PROJECT_ENV, ""),
            "service": os.getenv(RAILWAY_SERVICE_ENV, ""),
            "environment": os.getenv(RAILWAY_ENVIRONMENT_ENV, ""),
        }
    )
    if railway_config.token or railway_config.has_default_scope:
        integrations.append(
            _active_env_record("railway", railway_config.model_dump(exclude={"integration_id"}))
        )

    grafana_multi = _parse_instances_env("GRAFANA_INSTANCES", "grafana")
    if grafana_multi is not None:
        integrations.append(grafana_multi)
        grafana_endpoint = ""
        grafana_api_key = ""
    else:
        grafana_endpoint = os.getenv(GRAFANA_INSTANCE_URL_ENV, "").strip()
        grafana_api_key = resolve_env_credential(GRAFANA_READ_TOKEN_ENV)
    if grafana_endpoint and grafana_api_key:
        try:
            grafana_config = GrafanaIntegrationConfig.model_validate(
                {
                    "endpoint": grafana_endpoint,
                    "api_key": grafana_api_key,
                    "verify_ssl": os.getenv(GRAFANA_VERIFY_SSL_ENV, "true").strip().lower()
                    != "false",
                    "ca_bundle": os.getenv(GRAFANA_CA_BUNDLE_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="grafana")
        else:
            integrations.append(
                _active_env_record(
                    "grafana",
                    {
                        "endpoint": grafana_config.endpoint,
                        "api_key": grafana_config.api_key,
                        "verify_ssl": grafana_config.verify_ssl,
                        "ca_bundle": grafana_config.ca_bundle,
                    },
                )
            )

    datadog_multi = _parse_instances_env("DD_INSTANCES", "datadog")
    if datadog_multi is not None:
        integrations.append(datadog_multi)
        datadog_api_key = ""
        datadog_app_key = ""
        datadog_site = ""
    else:
        datadog_api_key = resolve_env_credential(DATADOG_API_KEY_ENV)
        datadog_app_key = resolve_env_credential(DATADOG_APP_KEY_ENV)
        datadog_site = (
            os.getenv(DATADOG_SITE_ENV, DEFAULT_DATADOG_SITE).strip() or DEFAULT_DATADOG_SITE
        )
    if datadog_api_key and datadog_app_key:
        try:
            datadog_config = DatadogIntegrationConfig.model_validate(
                {
                    "api_key": datadog_api_key,
                    "app_key": datadog_app_key,
                    "site": datadog_site,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="datadog")
        else:
            integrations.append(
                _active_env_record(
                    "datadog",
                    datadog_config.model_dump(exclude={"integration_id"}),
                )
            )

    groundcover_multi = _parse_instances_env("GROUNDCOVER_INSTANCES", "groundcover")
    if groundcover_multi is not None:
        integrations.append(groundcover_multi)
        groundcover_api_key = ""
    else:
        groundcover_api_key = resolve_env_credential(
            GROUNDCOVER_API_KEY_ENV
        ) or resolve_env_credential(GROUNDCOVER_MCP_TOKEN_ENV)
    if groundcover_api_key:
        # The groundcover config validates the MCP URL (HTTPS-or-loopback), which
        # can raise on a bad GROUNDCOVER_MCP_URL. Guard it so one malformed value
        # cannot abort discovery of every other env integration.
        try:
            groundcover_config = GroundcoverIntegrationConfig.model_validate(
                {
                    "api_key": groundcover_api_key,
                    "mcp_url": os.getenv(GROUNDCOVER_MCP_URL_ENV, "").strip(),
                    "tenant_uuid": os.getenv(GROUNDCOVER_TENANT_UUID_ENV, "").strip(),
                    "backend_id": os.getenv(GROUNDCOVER_BACKEND_ID_ENV, "").strip(),
                    "timezone": os.getenv(GROUNDCOVER_TIMEZONE_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="groundcover")
        else:
            integrations.append(
                _active_env_record(
                    "groundcover",
                    groundcover_config.model_dump(exclude={"integration_id"}),
                )
            )

    honeycomb_multi = _parse_instances_env("HONEYCOMB_INSTANCES", "honeycomb")
    if honeycomb_multi is not None:
        integrations.append(honeycomb_multi)
        honeycomb_api_key = ""
    else:
        honeycomb_api_key = resolve_env_credential(HONEYCOMB_API_KEY_ENV)
    if honeycomb_api_key:
        try:
            honeycomb_config = HoneycombIntegrationConfig.model_validate(
                {
                    "api_key": honeycomb_api_key,
                    "dataset": os.getenv(HONEYCOMB_DATASET_ENV, "").strip(),
                    "base_url": os.getenv(HONEYCOMB_BASE_URL_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="honeycomb")
        else:
            integrations.append(
                _active_env_record(
                    "honeycomb",
                    honeycomb_config.model_dump(exclude={"integration_id"}),
                )
            )

    new_relic_multi = _parse_instances_env(NEW_RELIC_INSTANCES_ENV, "new_relic")
    if new_relic_multi is not None:
        integrations.append(new_relic_multi)
        new_relic_api_key = ""
        new_relic_account_id = ""
        new_relic_base_url = ""
    else:
        new_relic_api_key = resolve_env_credential(NEW_RELIC_API_KEY_ENV)
        new_relic_account_id = os.getenv(NEW_RELIC_ACCOUNT_ID_ENV, "").strip()
        new_relic_base_url = os.getenv(NEW_RELIC_BASE_URL_ENV, "").strip()
    if new_relic_api_key and new_relic_account_id:
        try:
            new_relic_config = NewRelicIntegrationConfig.model_validate(
                {
                    "api_key": new_relic_api_key,
                    "account_id": new_relic_account_id,
                    "base_url": new_relic_base_url,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="new_relic")
        else:
            integrations.append(
                _active_env_record(
                    "new_relic",
                    new_relic_config.model_dump(exclude={"integration_id"}),
                )
            )

    coralogix_multi = _parse_instances_env("CORALOGIX_INSTANCES", "coralogix")
    if coralogix_multi is not None:
        integrations.append(coralogix_multi)
        coralogix_api_key = ""
    else:
        coralogix_api_key = resolve_env_credential(CORALOGIX_API_KEY_ENV)
    if coralogix_api_key:
        try:
            coralogix_config = CoralogixIntegrationConfig.model_validate(
                {
                    "api_key": coralogix_api_key,
                    "base_url": os.getenv(CORALOGIX_BASE_URL_ENV, "").strip(),
                    "application_name": os.getenv(CORALOGIX_APPLICATION_NAME_ENV, "").strip(),
                    "subsystem_name": os.getenv(CORALOGIX_SUBSYSTEM_NAME_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="coralogix")
        else:
            integrations.append(
                _active_env_record(
                    "coralogix",
                    coralogix_config.model_dump(exclude={"integration_id"}),
                )
            )

    aws_multi = _parse_instances_env("AWS_INSTANCES", "aws")
    if aws_multi is not None:
        integrations.append(aws_multi)
        aws_role_arn = ""
        aws_external_id = ""
        aws_region = "us-east-1"
        aws_access_key_id = ""
        aws_secret_access_key = ""
        aws_session_token = ""
    else:
        aws_role_arn = os.getenv(AWS_ROLE_ARN_ENV, "").strip()
        aws_external_id = os.getenv(AWS_EXTERNAL_ID_ENV, "").strip()
        aws_region = os.getenv(AWS_REGION_ENV, "us-east-1").strip() or "us-east-1"
        aws_access_key_id = resolve_env_credential(AWS_ACCESS_KEY_ID_ENV)
        aws_secret_access_key = resolve_env_credential(AWS_SECRET_ACCESS_KEY_ENV)
        aws_session_token = resolve_env_credential(AWS_SESSION_TOKEN_ENV)
    if aws_role_arn:
        try:
            aws_config = AWSIntegrationConfig.model_validate(
                {
                    "role_arn": aws_role_arn,
                    "external_id": aws_external_id,
                    "region": aws_region,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="aws")
        else:
            integrations.append(
                _active_env_record(
                    "aws",
                    {"region": aws_config.region},
                    role_arn=aws_config.role_arn,
                    external_id=aws_config.external_id,
                )
            )
    elif aws_access_key_id and aws_secret_access_key:
        try:
            aws_config = AWSIntegrationConfig.model_validate(
                {
                    "region": aws_region,
                    "credentials": {
                        "access_key_id": aws_access_key_id,
                        "secret_access_key": aws_secret_access_key,
                        "session_token": aws_session_token,
                    },
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="aws")
        else:
            aws_credentials = aws_config.credentials
            if aws_credentials is not None:
                integrations.append(
                    _active_env_record(
                        "aws",
                        {
                            "access_key_id": aws_credentials.access_key_id,
                            "secret_access_key": aws_credentials.secret_access_key,
                            "session_token": aws_credentials.session_token,
                            "region": aws_config.region,
                        },
                    )
                )

    github_mode = os.getenv(GITHUB_MCP_MODE_ENV, "streamable-http").strip() or "streamable-http"
    github_url = os.getenv(GITHUB_MCP_URL_ENV, "").strip()
    github_command = os.getenv(GITHUB_MCP_COMMAND_ENV, "").strip()
    github_args = os.getenv(GITHUB_MCP_ARGS_ENV, "").strip()
    github_auth_token = resolve_env_credential(GITHUB_MCP_AUTH_TOKEN_ENV)
    github_toolsets = os.getenv(GITHUB_MCP_TOOLSETS_ENV, "").strip()
    if (github_mode == "stdio" and github_command) or (github_mode != "stdio" and github_url):
        github_config = build_github_mcp_config(
            {
                "url": github_url,
                "mode": github_mode,
                "command": github_command,
                "args": [part for part in github_args.split() if part],
                "auth_token": github_auth_token,
                "toolsets": [part.strip() for part in github_toolsets.split(",") if part.strip()],
            }
        )
        integrations.append(
            _active_env_record(
                "github",
                github_config.model_dump(exclude={"integration_id"}),
            )
        )

    sentry_org_slug = os.getenv(SENTRY_ORGANIZATION_SLUG_ENV, "").strip()
    sentry_auth_token = resolve_env_credential(SENTRY_AUTH_TOKEN_ENV)
    if sentry_org_slug and sentry_auth_token:
        sentry_config = build_sentry_config(
            {
                "base_url": os.getenv(SENTRY_BASE_URL_ENV, DEFAULT_SENTRY_BASE_URL).strip()
                or DEFAULT_SENTRY_BASE_URL,
                "organization_slug": sentry_org_slug,
                "auth_token": sentry_auth_token,
                "project_slug": os.getenv(SENTRY_PROJECT_SLUG_ENV, "").strip(),
            }
        )
        integrations.append(
            _active_env_record(
                "sentry",
                sentry_config.model_dump(exclude={"integration_id"}),
            )
        )

    gitlab_access_token = resolve_env_credential(GITLAB_AUTH_TOKEN_ENV)
    if gitlab_access_token:
        gitlab_config = build_gitlab_config(
            {
                "base_url": os.getenv(GITLAB_BASE_URL_ENV, DEFAULT_GITLAB_BASE_URL).strip()
                or DEFAULT_GITLAB_BASE_URL,
                "auth_token": gitlab_access_token,
            }
        )
        integrations.append(_active_env_record("gitlab", gitlab_config.model_dump()))

    mongodb_connection_string = resolve_env_credential(MONGODB_CONNECTION_STRING_ENV)
    if mongodb_connection_string:
        mongodb_config = build_mongodb_config(
            {
                "connection_string": mongodb_connection_string,
                "database": os.getenv(MONGODB_DATABASE_ENV, "").strip(),
                "auth_source": os.getenv(MONGODB_AUTH_SOURCE_ENV, "admin").strip() or "admin",
                "tls": os.getenv(MONGODB_TLS_ENV, "true").strip().lower() in ("true", "1", "yes"),
            }
        )
        integrations.append(
            _active_env_record(
                "mongodb",
                mongodb_config.model_dump(exclude={"integration_id"}),
            )
        )

    redis_config = redis_config_from_env()
    if redis_config:
        integrations.append(
            _active_env_record(
                "redis",
                redis_config.model_dump(exclude={"integration_id"}),
            )
        )

    postgresql_host = os.getenv(POSTGRESQL_HOST_ENV, "").strip()
    postgresql_database = os.getenv(POSTGRESQL_DATABASE_ENV, "").strip()
    if postgresql_host and postgresql_database:
        postgresql_config = build_postgresql_config(
            {
                "host": postgresql_host,
                "port": int(_pg_port)
                if (_pg_port := os.getenv(POSTGRESQL_PORT_ENV, "").strip()) and _pg_port.isdigit()
                else 5432,
                "database": postgresql_database,
                "username": os.getenv(POSTGRESQL_USERNAME_ENV, "postgres").strip() or "postgres",
                "password": resolve_env_credential(POSTGRESQL_PASSWORD_ENV),
                "ssl_mode": os.getenv(POSTGRESQL_SSL_MODE_ENV, "prefer").strip() or "prefer",
            }
        )
        integrations.append(
            _active_env_record(
                "postgresql",
                postgresql_config.model_dump(exclude={"integration_id"}),
            )
        )

    argocd_multi = _parse_instances_env("ARGOCD_INSTANCES", "argocd")
    if argocd_multi is not None:
        integrations.append(argocd_multi)
        argocd_base_url = ""
        argocd_auth_token = ""
        argocd_username = ""
        argocd_password = ""
    else:
        argocd_base_url = os.getenv("ARGOCD_BASE_URL", "").strip()
        argocd_auth_token = resolve_env_credential("ARGOCD_AUTH_TOKEN") or resolve_env_credential(
            "ARGOCD_TOKEN"
        )
        argocd_username = os.getenv("ARGOCD_USERNAME", "").strip()
        argocd_password = resolve_env_credential("ARGOCD_PASSWORD")
    if argocd_base_url and (argocd_auth_token or (argocd_username and argocd_password)):
        try:
            argocd_config = ArgoCDIntegrationConfig.model_validate(
                {
                    "base_url": argocd_base_url,
                    "bearer_token": argocd_auth_token,
                    "username": argocd_username,
                    "password": argocd_password,
                    "project": os.getenv("ARGOCD_PROJECT", "").strip(),
                    "app_namespace": os.getenv("ARGOCD_APP_NAMESPACE", "").strip(),
                    "verify_ssl": os.getenv("ARGOCD_VERIFY_SSL", "true").strip(),
                }
            )
        except Exception as exc:
            # Invalid env-derived config: skip ArgoCD entry rather than fail
            # discovery, but report so operators can see the misconfig.
            _report_env_loader_failure(exc, integration="argocd")
        else:
            integrations.append(
                _active_env_record(
                    "argocd",
                    argocd_config.model_dump(exclude={"integration_id"}),
                )
            )

    helm_env_enabled = os.getenv(OSRE_HELM_INTEGRATION_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if helm_env_enabled:
        try:
            helm_env_config = HelmIntegrationConfig.model_validate(
                {
                    "helm_path": os.getenv(HELM_PATH_ENV, "helm").strip() or "helm",
                    "kube_context": os.getenv(HELM_KUBE_CONTEXT_ENV, "").strip(),
                    "kubeconfig": os.getenv(HELM_KUBECONFIG_ENV, "").strip(),
                    "default_namespace": os.getenv(HELM_NAMESPACE_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="helm")
        else:
            integrations.append(
                _active_env_record(
                    "helm",
                    helm_env_config.model_dump(exclude={"integration_id"}),
                )
            )

    vercel_api_token = resolve_env_credential(VERCEL_API_TOKEN_ENV)
    if vercel_api_token:
        try:
            vercel_config = VercelConfig.model_validate(
                {
                    "api_token": vercel_api_token,
                    "team_id": os.getenv(VERCEL_TEAM_ID_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="vercel")
        else:
            integrations.append(
                _active_env_record(
                    "vercel",
                    vercel_config.model_dump(exclude={"integration_id"}),
                )
            )

    opsgenie_api_key = resolve_env_credential("OPSGENIE_API_KEY")
    if opsgenie_api_key:
        try:
            opsgenie_config = OpsGenieIntegrationConfig.model_validate(
                {
                    "api_key": opsgenie_api_key,
                    "region": os.getenv("OPSGENIE_REGION", "us").strip() or "us",
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="opsgenie")
        else:
            integrations.append(
                _active_env_record(
                    "opsgenie",
                    opsgenie_config.model_dump(exclude={"integration_id"}),
                )
            )

    pagerduty_api_key = resolve_env_credential(PAGERDUTY_API_KEY_ENV)
    if pagerduty_api_key:
        try:
            _envs: dict[str, Any] = {"api_key": pagerduty_api_key}
            base_url = os.getenv(PAGERDUTY_BASE_URL_ENV, "").strip()
            if base_url:
                _envs["base_url"] = base_url
            pagerduty_config = PagerDutyIntegrationConfig.model_validate(_envs)
        except Exception as exc:
            _report_env_loader_failure(exc, integration="pagerduty")
        else:
            integrations.append(
                _active_env_record(
                    "pagerduty",
                    pagerduty_config.model_dump(exclude={"integration_id"}),
                )
            )

    incident_io_api_key = resolve_env_credential(INCIDENT_IO_API_KEY_ENV)
    if incident_io_api_key:
        try:
            incident_io_config = IncidentIoIntegrationConfig.model_validate(
                {
                    "api_key": incident_io_api_key,
                    "base_url": os.getenv(INCIDENT_IO_BASE_URL_ENV, "").strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="incident_io")
        else:
            integrations.append(
                _active_env_record(
                    "incident_io",
                    incident_io_config.model_dump(exclude={"integration_id"}),
                )
            )

    jira_base_url = os.getenv("JIRA_BASE_URL", "").strip()
    jira_email = os.getenv("JIRA_EMAIL", "").strip()
    jira_api_token = resolve_env_credential("JIRA_API_TOKEN")
    jira_project_key = os.getenv("JIRA_PROJECT_KEY", "").strip()
    if jira_base_url and jira_email and jira_api_token:
        try:
            jira_config = JiraIntegrationConfig.model_validate(
                {
                    "base_url": jira_base_url,
                    "email": jira_email,
                    "api_token": jira_api_token,
                    "project_key": jira_project_key,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="jira")
        else:
            integrations.append(
                _active_env_record(
                    "jira",
                    jira_config.model_dump(exclude={"integration_id"}),
                )
            )

    google_docs_credentials_file = os.getenv(GOOGLE_CREDENTIALS_FILE_ENV, "").strip()
    google_docs_folder_id = os.getenv(GOOGLE_DRIVE_FOLDER_ID_ENV, "").strip()
    if google_docs_credentials_file and google_docs_folder_id:
        try:
            google_docs_config = GoogleDocsIntegrationConfig.model_validate(
                {
                    "credentials_file": google_docs_credentials_file,
                    "folder_id": google_docs_folder_id,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="google_docs")
        else:
            integrations.append(
                _active_env_record(
                    "google_docs",
                    {
                        "credentials_file": google_docs_config.credentials_file,
                        "folder_id": str(google_docs_config.folder_id).strip(),
                    },
                )
            )

    servicenow_instance_url = os.getenv(SERVICENOW_INSTANCE_URL_ENV, "").strip()
    servicenow_username = os.getenv(SERVICENOW_USERNAME_ENV, "").strip()
    # Resolve the password (env, then OS keyring) only once the cheap env vars
    # are present, so unconfigured installs never pay a keyring roundtrip here.
    servicenow_password = (
        resolve_env_credential(SERVICENOW_PASSWORD_ENV)
        if servicenow_instance_url and servicenow_username
        else ""
    )
    if servicenow_instance_url and servicenow_username and servicenow_password:
        try:
            servicenow_config = ServiceNowIntegrationConfig.model_validate(
                {
                    "instance_url": servicenow_instance_url,
                    "username": servicenow_username,
                    "password": servicenow_password,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="servicenow")
        else:
            integrations.append(
                _active_env_record(
                    "servicenow",
                    servicenow_config.model_dump(exclude={"integration_id"}),
                )
            )

    discord_bot_token = resolve_env_credential(DISCORD_BOT_TOKEN_ENV)
    if discord_bot_token:
        try:
            discord_config = DiscordBotConfig.model_validate(
                {
                    "bot_token": discord_bot_token,
                    "application_id": os.getenv(DISCORD_APPLICATION_ID_ENV, "").strip(),
                    "public_key": os.getenv(DISCORD_PUBLIC_KEY_ENV, "").strip(),
                    "default_channel_id": os.getenv(DISCORD_DEFAULT_CHANNEL_ID_ENV, "").strip()
                    or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="discord")
        else:
            integrations.append(_active_env_record("discord", discord_config.model_dump()))

    airflow_config = airflow_config_from_env()
    if airflow_config is not None:
        integrations.append(_active_env_record("airflow", airflow_config.model_dump()))

    telegram_bot_token = resolve_env_credential(TELEGRAM_BOT_TOKEN_ENV)
    if telegram_bot_token:
        try:
            tg_config = TelegramBotConfig.model_validate(
                {
                    "bot_token": telegram_bot_token,
                    "default_chat_id": os.getenv(TELEGRAM_DEFAULT_CHAT_ID_ENV, "").strip() or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="telegram")
        else:
            integrations.append(_active_env_record("telegram", tg_config.model_dump()))

    # PAT is keyring-backed via wizard sync_env_secret; webhook URL stays store/env only.
    rocketchat_auth_token = resolve_env_credential(ROCKETCHAT_AUTH_TOKEN_ENV)
    rocketchat_webhook_url = os.getenv("ROCKETCHAT_WEBHOOK_URL", "").strip()
    if rocketchat_auth_token or rocketchat_webhook_url:
        try:
            rocketchat_config = RocketChatConfig.model_validate(
                {
                    "server_url": os.getenv(ROCKETCHAT_SERVER_URL_ENV, "").strip(),
                    "auth_token": rocketchat_auth_token,
                    "user_id": os.getenv(ROCKETCHAT_USER_ID_ENV, "").strip(),
                    "webhook_url": rocketchat_webhook_url,
                    "default_channel": os.getenv(ROCKETCHAT_DEFAULT_CHANNEL_ENV, "").strip()
                    or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="rocketchat")
        else:
            integrations.append(_active_env_record("rocketchat", rocketchat_config.model_dump()))

    # Private key is keyring-backed via wizard sync_env_secret; the rest stay
    # store/env only (relay_url, default_channel, auth_tag, buzz_path).
    buzz_private_key = resolve_env_credential(BUZZ_PRIVATE_KEY_ENV)
    if buzz_private_key:
        try:
            buzz_config = BuzzConfig.model_validate(
                {
                    "relay_url": os.getenv(BUZZ_RELAY_URL_ENV, "").strip()
                    or "http://localhost:3000",
                    "private_key": buzz_private_key,
                    "default_channel": os.getenv(BUZZ_DEFAULT_CHANNEL_ENV, "").strip(),
                    "auth_tag": os.getenv(BUZZ_AUTH_TAG_ENV, "").strip(),
                    "buzz_path": os.getenv(BUZZ_PATH_ENV, "").strip() or "buzz",
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="buzz")
        else:
            integrations.append(
                _active_env_record("buzz", buzz_config.model_dump(exclude={"integration_id"}))
            )

    slack_bot_token = resolve_env_credential(SLACK_BOT_TOKEN_ENV)
    slack_webhook_url = os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip()
    if slack_bot_token or slack_webhook_url:
        slack_credentials = {
            "webhook_url": slack_webhook_url,
            "bot_token": slack_bot_token,
            "app_token": resolve_env_credential(SLACK_APP_TOKEN_ENV),
            "default_chat_id": os.getenv(SLACK_DEFAULT_CHAT_ID_ENV, "").strip(),
        }
        slack_view, _slack_key = _classify_slack(slack_credentials, record_id="env:slack")
        if slack_view is not None:
            integrations.append(_active_env_record("slack", slack_view))

    smtp_host = os.getenv(SMTP_HOST_ENV, "").strip()
    if smtp_host:
        try:
            smtp_config = SMTPIntegrationConfig.model_validate(
                {
                    "host": smtp_host,
                    "port": os.getenv(SMTP_PORT_ENV, "").strip() or 587,
                    "security": os.getenv(SMTP_SECURITY_ENV, "").strip() or "starttls",
                    "username": os.getenv(SMTP_USERNAME_ENV, "").strip(),
                    "password": resolve_env_credential(SMTP_PASSWORD_ENV),
                    "from_address": os.getenv(SMTP_FROM_ADDRESS_ENV, "").strip(),
                    "default_to": os.getenv(SMTP_DEFAULT_TO_ENV, "").strip() or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="smtp")
        else:
            integrations.append(_active_env_record("smtp", smtp_config.model_dump()))

    # Shared Twilio account credentials — consumed by both the WhatsApp and
    # the SMS env-bootstrap blocks below.
    twilio_account_sid = resolve_env_credential(TWILIO_ACCOUNT_SID_ENV)
    twilio_auth_token = resolve_env_credential(TWILIO_AUTH_TOKEN_ENV)

    whatsapp_from_number = os.getenv(TWILIO_WHATSAPP_FROM_ENV, "").strip()
    if twilio_account_sid and twilio_auth_token and whatsapp_from_number:
        try:
            wa_config = WhatsAppConfig.model_validate(
                {
                    "account_sid": twilio_account_sid,
                    "auth_token": twilio_auth_token,
                    "from_number": whatsapp_from_number,
                    "default_to": os.getenv(WHATSAPP_DEFAULT_TO_ENV, "").strip() or None,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="whatsapp")
        else:
            integrations.append(_active_env_record("whatsapp", wa_config.model_dump()))

    # Twilio SMS integration — independent of the legacy WhatsApp record.
    # Hydrated when account+token are present AND an SMS sender is set
    # (a from_number or a Messaging Service SID).
    twilio_sms_from = os.getenv(TWILIO_SMS_FROM_ENV, "").strip()
    twilio_sms_messaging_service = os.getenv(TWILIO_SMS_MESSAGING_SERVICE_SID_ENV, "").strip()
    if (
        twilio_account_sid
        and twilio_auth_token
        and (twilio_sms_from or twilio_sms_messaging_service)
    ):
        twilio_payload: dict[str, Any] = {
            "account_sid": twilio_account_sid,
            "auth_token": twilio_auth_token,
            "sms": {
                "enabled": True,
                "from_number": twilio_sms_from,
                "messaging_service_sid": twilio_sms_messaging_service,
                "default_to": os.getenv(TWILIO_SMS_DEFAULT_TO_ENV, "").strip() or None,
            },
        }
        try:
            twilio_config = TwilioIntegrationConfig.model_validate(twilio_payload)
        except Exception as exc:
            _report_env_loader_failure(exc, integration="twilio")
        else:
            integrations.append(
                _active_env_record(
                    "twilio",
                    twilio_config.model_dump(exclude={"integration_id"}),
                )
            )

    atlas_pub = resolve_env_credential(MONGODB_ATLAS_PUBLIC_KEY_ENV)
    atlas_priv = resolve_env_credential(MONGODB_ATLAS_PRIVATE_KEY_ENV)
    atlas_project = os.getenv(MONGODB_ATLAS_PROJECT_ID_ENV, "").strip()
    if atlas_pub and atlas_priv and atlas_project:
        try:
            atlas_config = build_mongodb_atlas_config(
                {
                    "api_public_key": atlas_pub,
                    "api_private_key": atlas_priv,
                    "project_id": atlas_project,
                    "base_url": os.getenv(
                        MONGODB_ATLAS_BASE_URL_ENV, "https://cloud.mongodb.com/api/atlas/v2"
                    ).strip(),
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="mongodb_atlas")
        else:
            integrations.append(
                _active_env_record(
                    "mongodb_atlas",
                    atlas_config.model_dump(exclude={"integration_id"}),
                )
            )

    try:
        posthog_config = posthog_config_from_env()
        if posthog_config is not None:
            integrations.append(
                _active_env_record(
                    "posthog",
                    posthog_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception as exc:
        _report_env_loader_failure(exc, integration="posthog")

    posthog_mcp_mode = os.getenv("POSTHOG_MCP_MODE", "streamable-http").strip().lower()
    posthog_mcp_mode = posthog_mcp_mode or "streamable-http"
    posthog_mcp_command = os.getenv("POSTHOG_MCP_COMMAND", "").strip()
    posthog_mcp_token = resolve_env_credential(POSTHOG_MCP_AUTH_TOKEN_ENV)
    posthog_mcp_url = os.getenv(POSTHOG_MCP_URL_ENV, "").strip()
    if posthog_mcp_mode != "stdio" and posthog_mcp_token and not posthog_mcp_url:
        posthog_mcp_url = DEFAULT_POSTHOG_MCP_URL
    if (posthog_mcp_mode == "stdio" and posthog_mcp_command) or (
        posthog_mcp_mode != "stdio" and posthog_mcp_url and posthog_mcp_token
    ):
        read_only_env = os.getenv("POSTHOG_MCP_READ_ONLY", "").strip().lower()
        read_only = read_only_env not in ("false", "0", "no") if read_only_env else True
        try:
            posthog_mcp_config = build_posthog_mcp_config(
                {
                    "url": posthog_mcp_url,
                    "mode": posthog_mcp_mode,
                    "command": posthog_mcp_command,
                    "args": [
                        part for part in os.getenv("POSTHOG_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": posthog_mcp_token,
                    "organization_id": os.getenv("POSTHOG_MCP_ORGANIZATION_ID", "").strip(),
                    "project_id": os.getenv(POSTHOG_MCP_PROJECT_ID_ENV, "").strip(),
                    "features": os.getenv("POSTHOG_MCP_FEATURES", "").strip(),
                    "read_only": read_only,
                }
            )
            integrations.append(
                _active_env_record(
                    "posthog_mcp",
                    {
                        **posthog_mcp_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="posthog_mcp")

    sentry_mcp_mode = os.getenv("SENTRY_MCP_MODE", "streamable-http").strip().lower()
    sentry_mcp_mode = sentry_mcp_mode or "streamable-http"
    sentry_mcp_command = os.getenv("SENTRY_MCP_COMMAND", "").strip()
    sentry_mcp_token = resolve_env_credential(SENTRY_MCP_AUTH_TOKEN_ENV)
    sentry_mcp_url = os.getenv(SENTRY_MCP_URL_ENV, "").strip()
    if sentry_mcp_mode != "stdio" and sentry_mcp_token and not sentry_mcp_url:
        sentry_mcp_url = DEFAULT_SENTRY_MCP_URL
    if (sentry_mcp_mode == "stdio" and sentry_mcp_command) or (
        sentry_mcp_mode != "stdio" and sentry_mcp_url and sentry_mcp_token
    ):
        try:
            sentry_mcp_config = build_sentry_mcp_config(
                {
                    "url": sentry_mcp_url,
                    "mode": sentry_mcp_mode,
                    "command": sentry_mcp_command,
                    "args": [
                        part for part in os.getenv("SENTRY_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": sentry_mcp_token,
                    "host": os.getenv(SENTRY_MCP_HOST_ENV, "").strip(),
                    "organization_slug": os.getenv("SENTRY_MCP_ORGANIZATION_SLUG", "").strip(),
                    "project_slug": os.getenv("SENTRY_MCP_PROJECT_SLUG", "").strip(),
                    "skills": os.getenv("SENTRY_MCP_SKILLS", "").strip(),
                }
            )
            integrations.append(
                _active_env_record(
                    "sentry_mcp",
                    {
                        **sentry_mcp_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="sentry_mcp")

    x_mcp_mode = os.getenv("X_MCP_MODE", "streamable-http").strip().lower()
    x_mcp_mode = x_mcp_mode or "streamable-http"
    x_mcp_command = os.getenv("X_MCP_COMMAND", "").strip()
    x_mcp_token = resolve_env_credential(X_MCP_AUTH_TOKEN_ENV)
    x_mcp_bearer_token = resolve_env_credential("X_BEARER_TOKEN")
    x_mcp_url = os.getenv(X_MCP_URL_ENV, "").strip()
    if (x_mcp_mode == "stdio" and x_mcp_command) or (x_mcp_mode != "stdio" and x_mcp_url):
        try:
            x_mcp_config = build_x_mcp_config(
                {
                    "url": x_mcp_url,
                    "mode": x_mcp_mode,
                    "command": x_mcp_command,
                    "args": [part for part in os.getenv("X_MCP_ARGS", "").strip().split() if part],
                    "auth_token": x_mcp_token,
                    "bearer_token": x_mcp_bearer_token,
                }
            )
            integrations.append(
                _active_env_record(
                    "x_mcp",
                    {
                        **x_mcp_config.model_dump(exclude={"integration_id"}),
                        "connection_verified": True,
                    },
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="x_mcp")

    mariadb_host = os.getenv(MARIADB_HOST_ENV, "").strip()
    mariadb_database = os.getenv(MARIADB_DATABASE_ENV, "").strip()
    if mariadb_host and mariadb_database:
        try:
            mariadb_config = build_mariadb_config(
                {
                    "host": mariadb_host,
                    "port": os.getenv(MARIADB_PORT_ENV, "3306").strip(),
                    "database": mariadb_database,
                    "username": os.getenv(MARIADB_USERNAME_ENV, "").strip(),
                    "password": resolve_env_credential(MARIADB_PASSWORD_ENV),
                    "ssl": os.getenv(MARIADB_SSL_ENV, "true").strip().lower()
                    in ("true", "1", "yes"),
                }
            )
            integrations.append(
                _active_env_record(
                    "mariadb",
                    mariadb_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="mariadb")

    dagster_endpoint = os.getenv(DAGSTER_ENDPOINT_ENV, "").strip()
    if dagster_endpoint:
        try:
            dagster_config = build_dagster_config(
                {
                    "endpoint": dagster_endpoint,
                    "api_token": resolve_env_credential(DAGSTER_API_TOKEN_ENV),
                }
            )
            integrations.append(
                _active_env_record(
                    "dagster",
                    dagster_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="dagster")

    rabbitmq_host = os.getenv("RABBITMQ_HOST", "").strip()
    rabbitmq_username = os.getenv("RABBITMQ_USERNAME", "").strip()
    if rabbitmq_host and rabbitmq_username:
        try:
            rabbitmq_config = build_rabbitmq_config(
                {
                    "host": rabbitmq_host,
                    "management_port": os.getenv("RABBITMQ_MANAGEMENT_PORT", "15672").strip(),
                    "username": rabbitmq_username,
                    "password": resolve_env_credential("RABBITMQ_PASSWORD"),
                    "vhost": os.getenv("RABBITMQ_VHOST", "/").strip(),
                    "ssl": os.getenv("RABBITMQ_SSL", "false").strip().lower()
                    in ("true", "1", "yes"),
                    "verify_ssl": os.getenv("RABBITMQ_VERIFY_SSL", "true").strip().lower()
                    in ("true", "1", "yes"),
                }
            )
            integrations.append(
                _active_env_record(
                    "rabbitmq",
                    rabbitmq_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="rabbitmq")

    try:
        rds_config = rds_config_from_env()
    except Exception as exc:
        rds_config = None
        _report_env_loader_failure(exc, integration="rds")
    if rds_config is not None and rds_config.is_configured:
        integrations.append(
            _active_env_record(
                "rds",
                rds_config.model_dump(exclude={"integration_id"}),
            )
        )

    bs_endpoint = os.getenv(BETTERSTACK_QUERY_ENDPOINT_ENV, "").strip()
    bs_username = os.getenv(BETTERSTACK_USERNAME_ENV, "").strip()
    if bs_endpoint and bs_username:
        try:
            bs_config = build_betterstack_config(
                {
                    "query_endpoint": bs_endpoint,
                    "username": bs_username,
                    "password": resolve_env_credential(BETTERSTACK_PASSWORD_ENV),
                    "sources": os.getenv(BETTERSTACK_SOURCES_ENV, ""),
                }
            )
            integrations.append(
                _active_env_record(
                    "betterstack",
                    bs_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="betterstack")

    mysql_host = os.getenv(MYSQL_HOST_ENV, "").strip()
    mysql_database = os.getenv(MYSQL_DATABASE_ENV, "").strip()
    if mysql_host and mysql_database:
        mysql_config = build_mysql_config(
            {
                "host": mysql_host,
                "port": int(_mysql_port)
                if (_mysql_port := os.getenv(MYSQL_PORT_ENV, "").strip()) and _mysql_port.isdigit()
                else 3306,
                "database": mysql_database,
                "username": os.getenv(MYSQL_USERNAME_ENV, "root").strip() or "root",
                "password": resolve_env_credential(MYSQL_PASSWORD_ENV),
                "ssl_mode": os.getenv(MYSQL_SSL_MODE_ENV, "preferred").strip() or "preferred",
            }
        )
        integrations.append(
            _active_env_record(
                "mysql",
                mysql_config.model_dump(exclude={"integration_id"}),
            )
        )

    azure_sql_server = os.getenv(AZURE_SQL_SERVER_ENV, "").strip()
    azure_sql_database = os.getenv(AZURE_SQL_DATABASE_ENV, "").strip()
    if azure_sql_server and azure_sql_database:
        _az_port = os.getenv(AZURE_SQL_PORT_ENV, "").strip()
        azure_sql_config = build_azure_sql_config(
            {
                "server": azure_sql_server,
                "port": int(_az_port) if _az_port and _az_port.isdigit() else 1433,
                "database": azure_sql_database,
                "username": os.getenv(AZURE_SQL_USERNAME_ENV, "").strip(),
                "password": resolve_env_credential(AZURE_SQL_PASSWORD_ENV),
                "driver": os.getenv(AZURE_SQL_DRIVER_ENV, "ODBC Driver 18 for SQL Server").strip(),
                "encrypt": os.getenv(AZURE_SQL_ENCRYPT_ENV, "true").strip().lower()
                in ("true", "1", "yes"),
            }
        )
        integrations.append(
            _active_env_record(
                "azure_sql",
                azure_sql_config.model_dump(exclude={"integration_id"}),
            )
        )

    bitbucket_workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    if bitbucket_workspace:
        integrations.append(
            _active_env_record(
                "bitbucket",
                {
                    "workspace": bitbucket_workspace,
                    "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
                    "app_password": resolve_env_credential("BITBUCKET_APP_PASSWORD"),
                    "base_url": os.getenv(
                        "BITBUCKET_BASE_URL", "https://api.bitbucket.org/2.0"
                    ).strip()
                    or "https://api.bitbucket.org/2.0",
                    "max_results": safe_int(os.getenv("BITBUCKET_MAX_RESULTS", "25"), 25),
                },
            )
        )

    snowflake_account = (
        os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "").strip()
        or os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
    )
    snowflake_token = resolve_env_credential("SNOWFLAKE_TOKEN")
    if snowflake_account and snowflake_token:
        integrations.append(
            _active_env_record(
                "snowflake",
                {
                    "account_identifier": snowflake_account,
                    "user": os.getenv("SNOWFLAKE_USER", "").strip(),
                    "password": resolve_env_credential("SNOWFLAKE_PASSWORD"),
                    "token": snowflake_token,
                    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "").strip(),
                    "role": os.getenv("SNOWFLAKE_ROLE", "").strip(),
                    "database": os.getenv("SNOWFLAKE_DATABASE", "").strip(),
                    "schema": os.getenv("SNOWFLAKE_SCHEMA", "").strip(),
                    "max_results": safe_int(os.getenv("SNOWFLAKE_MAX_RESULTS", "50"), 50),
                },
            )
        )

    azure_workspace_id = os.getenv(AZURE_LOG_ANALYTICS_WORKSPACE_ID_ENV, "").strip()
    azure_access_token = resolve_env_credential(AZURE_LOG_ANALYTICS_TOKEN_ENV)
    if azure_workspace_id and azure_access_token:
        integrations.append(
            _active_env_record(
                "azure",
                {
                    "workspace_id": azure_workspace_id,
                    "access_token": azure_access_token,
                    "endpoint": (
                        os.getenv(
                            AZURE_LOG_ANALYTICS_ENDPOINT_ENV,
                            AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
                        ).strip()
                        or AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT
                    ),
                    "tenant_id": os.getenv(AZURE_TENANT_ID_ENV, "").strip(),
                    "subscription_id": os.getenv(AZURE_SUBSCRIPTION_ID_ENV, "").strip(),
                    "max_results": safe_int(
                        os.getenv(AZURE_MAX_RESULTS_ENV, str(AZURE_MAX_RESULTS_DEFAULT)),
                        AZURE_MAX_RESULTS_DEFAULT,
                    ),
                },
            )
        )

    openobserve_url = os.getenv("OPENOBSERVE_URL", "").strip()
    openobserve_token = resolve_env_credential("OPENOBSERVE_TOKEN")
    openobserve_username = os.getenv("OPENOBSERVE_USERNAME", "").strip()
    openobserve_password = resolve_env_credential("OPENOBSERVE_PASSWORD")
    if openobserve_url and (openobserve_token or (openobserve_username and openobserve_password)):
        integrations.append(
            _active_env_record(
                "openobserve",
                {
                    "base_url": openobserve_url.rstrip("/"),
                    "org": os.getenv("OPENOBSERVE_ORG", "default").strip() or "default",
                    "api_token": openobserve_token,
                    "username": openobserve_username,
                    "password": openobserve_password,
                    "stream": os.getenv("OPENOBSERVE_STREAM", "").strip(),
                    "max_results": safe_int(os.getenv("OPENOBSERVE_MAX_RESULTS", "100"), 100),
                },
            )
        )

    opensearch_url = os.getenv(OPENSEARCH_URL_ENV, "").strip()
    if opensearch_url:
        integrations.append(
            _active_env_record(
                "opensearch",
                {
                    "url": opensearch_url.rstrip("/"),
                    "api_key": resolve_env_credential(OPENSEARCH_API_KEY_ENV),
                    "username": os.getenv(OPENSEARCH_USERNAME_ENV, "").strip(),
                    "password": resolve_env_credential(OPENSEARCH_PASSWORD_ENV),
                    "index_pattern": os.getenv("OPENSEARCH_INDEX_PATTERN", "*").strip() or "*",
                    "max_results": safe_int(os.getenv("OPENSEARCH_MAX_RESULTS", "100"), 100),
                },
            )
        )

    alertmanager_url = os.getenv(ALERTMANAGER_URL_ENV, "").strip().rstrip("/")
    if alertmanager_url:
        try:
            alertmanager_config = AlertmanagerIntegrationConfig.model_validate(
                {
                    "base_url": alertmanager_url,
                    "bearer_token": resolve_env_credential(ALERTMANAGER_BEARER_TOKEN_ENV),
                    "username": os.getenv(ALERTMANAGER_USERNAME_ENV, "").strip(),
                    "password": resolve_env_credential(ALERTMANAGER_PASSWORD_ENV),
                }
            )
            integrations.append(
                _active_env_record(
                    "alertmanager",
                    alertmanager_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="alertmanager")

    _kubeconfig_path = os.getenv(KUBECONFIG_PATH_ENV, "").strip()
    _kubeconfig_content = resolve_env_credential(KUBECONFIG_CONTENT_ENV)
    if _kubeconfig_path or _kubeconfig_content:
        try:
            kubernetes_config = KubernetesIntegrationConfig.model_validate(
                {
                    "kubeconfig_path": _kubeconfig_path,
                    "kubeconfig": _kubeconfig_content,
                    "context": os.getenv(KUBECONFIG_CONTEXT_ENV, "").strip(),
                    "namespace": os.getenv(KUBECONFIG_NAMESPACE_ENV, "default").strip()
                    or "default",
                }
            )
            integrations.append(
                _active_env_record(
                    "kubernetes",
                    kubernetes_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="kubernetes")

    victoria_logs_url = os.getenv("VICTORIA_LOGS_URL", "").strip().rstrip("/")
    if victoria_logs_url:
        try:
            victoria_logs_config = VictoriaLogsIntegrationConfig.model_validate(
                {
                    "base_url": victoria_logs_url,
                    "tenant_id": os.getenv("VICTORIA_LOGS_TENANT_ID"),
                }
            )
            integrations.append(
                _active_env_record(
                    "victoria_logs",
                    victoria_logs_config.model_dump(exclude={"integration_id"}),
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="victoria_logs")

    splunk_multi = _parse_instances_env("SPLUNK_INSTANCES", "splunk")
    if splunk_multi is not None:
        integrations.append(splunk_multi)
    else:
        splunk_url = os.getenv("SPLUNK_URL", "").strip()
        splunk_token = resolve_env_credential("SPLUNK_TOKEN")
        if splunk_url and splunk_token:
            try:
                splunk_config = SplunkIntegrationConfig.model_validate(
                    {
                        "base_url": splunk_url,
                        "token": splunk_token,
                        "index": os.getenv("SPLUNK_INDEX", "main").strip(),
                        "verify_ssl": os.getenv("SPLUNK_VERIFY_SSL", "true").strip().lower()
                        != "false",
                        "ca_bundle": os.getenv("SPLUNK_CA_BUNDLE", "").strip(),
                    }
                )
            except Exception as exc:
                _report_env_loader_failure(exc, integration="splunk")
            else:
                integrations.append(
                    _active_env_record(
                        "splunk",
                        splunk_config.model_dump(exclude={"integration_id"}),
                    )
                )

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_service_key = resolve_env_credential("SUPABASE_SERVICE_KEY")
    if supabase_url and supabase_service_key:
        try:
            sb_config = build_supabase_config(
                {"url": supabase_url, "service_key": supabase_service_key}
            )
            integrations.append(
                _active_env_record(
                    "supabase",
                    {"project_url": sb_config.url},
                )
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="supabase")

    try:
        signoz_config = signoz_config_from_env()
        if signoz_config is not None and signoz_config.is_configured:
            integrations.append(
                _active_env_record(
                    "signoz",
                    signoz_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception as exc:
        _report_env_loader_failure(exc, integration="signoz")

    try:
        jenkins_config = jenkins_config_from_env()
        if jenkins_config is not None and jenkins_config.is_configured:
            integrations.append(
                _active_env_record(
                    "jenkins",
                    jenkins_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception as exc:
        _report_env_loader_failure(exc, integration="jenkins")

    try:
        tempo_config = tempo_config_from_env()
        if tempo_config is not None and tempo_config.is_configured:
            integrations.append(
                _active_env_record(
                    "tempo",
                    tempo_config.model_dump(exclude={"integration_id"}),
                )
            )
    except Exception as exc:
        _report_env_loader_failure(exc, integration="tempo")

    temporal_url = os.getenv(TEMPORAL_BASE_URL_ENV, "").strip()
    temporal_namespace = os.getenv(TEMPORAL_NAMESPACE_ENV, "default").strip()
    if temporal_url and temporal_namespace:
        try:
            temporal_config = TemporalConfig.model_validate(
                {
                    "base_url": temporal_url,
                    "api_key": resolve_env_credential(TEMPORAL_API_KEY_ENV),
                    "namespace": temporal_namespace,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="temporal")
        else:
            integrations.append(
                _active_env_record(
                    "temporal",
                    temporal_config.model_dump(),
                )
            )

    yandex_cloud_folder = os.getenv(YC_FOLDER_ID_ENV, "").strip()
    yandex_cloud_use_metadata = os.getenv(YC_USE_METADATA_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # A folder alone is not a configuration: every read is folder-scoped, but it
    # still needs a credential. On an instance the metadata service is the
    # credential, and it also knows the folder, so that flag stands on its own.
    if yandex_cloud_folder or yandex_cloud_use_metadata:
        try:
            yandex_cloud_config = YandexCloudIntegrationConfig.model_validate(
                {
                    "folder_id": yandex_cloud_folder,
                    "cloud_id": os.getenv(YC_CLOUD_ID_ENV, "").strip(),
                    "sa_key_file": os.getenv(YC_SA_KEY_FILE_ENV, "").strip(),
                    "sa_key": resolve_env_credential(YC_SA_KEY_ENV),
                    "oauth_token": resolve_env_credential(YC_TOKEN_ENV),
                    "iam_token": resolve_env_credential(YC_IAM_TOKEN_ENV),
                    "use_metadata": yandex_cloud_use_metadata,
                }
            )
        except Exception as exc:
            _report_env_loader_failure(exc, integration="yandex_cloud")
        else:
            integrations.append(
                _active_env_record(
                    "yandex_cloud",
                    yandex_cloud_config.model_dump(exclude={"integration_id"}),
                )
            )

    return integrations


def merge_local_integrations(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge local store and env integrations, preferring store entries by service."""
    return merge_integrations_by_service(env_integrations, store_integrations)


def merge_integrations_by_service(
    *integration_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge integration records by service, letting later groups override earlier ones."""
    merged_by_service: dict[str, dict[str, Any]] = {}
    for integration_group in integration_groups:
        for integration in integration_group:
            service = str(integration.get("service", "")).strip()
            if service:
                merged_by_service[service] = integration
    return list(merged_by_service.values())


def _effective_entry(source: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"source": source, "config": config}


def _config_as_dict(config: Any) -> dict[str, Any] | None:
    """Normalize a classified config (BaseModel or dict) to a plain dict."""
    from pydantic import BaseModel

    if isinstance(config, BaseModel):
        return config.model_dump(exclude_none=True)
    if isinstance(config, dict) and config:
        return config
    return None


def _publish_classified_effective_service(
    effective: dict[str, dict[str, Any]],
    classified_integrations: dict[str, Any],
    source_by_service: dict[str, str],
    service: str,
) -> None:
    """Copy a directly classified service into the effective view."""
    resolved_integration = classified_integrations.get(service)
    if resolved_integration is None:
        spec = INTEGRATION_SPECS_BY_SERVICE.get(service)
        if spec is not None:
            for member in spec.family_members:
                resolved_integration = classified_integrations.get(member)
                if resolved_integration is not None:
                    break
    config_dict = _config_as_dict(resolved_integration)
    if config_dict is None:
        return

    effective[service] = _effective_entry(
        source_by_service.get(service, "local env"),
        config_dict,
    )
    all_instances = classified_integrations.get(f"_all_{service}_instances")
    if _should_publish_instance_siblings(all_instances) and isinstance(all_instances, list):
        # Convert any BaseModel configs to dicts in the instances list
        normalized_instances = [
            {**inst, "config": _config_as_dict(inst.get("config")) or {}}
            if isinstance(inst, dict)
            else inst
            for inst in all_instances
        ]
        effective[service]["instances"] = normalized_instances


def _service_metadata(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    source_by_service: dict[str, str] = {}
    store_integration_by_service: dict[str, dict[str, Any]] = {}

    for integration in env_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local env"

    for integration in store_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local store"
            store_integration_by_service.setdefault(service, integration)

    return source_by_service, store_integration_by_service


def _raw_credentials(config: dict[str, Any]) -> dict[str, Any]:
    credentials = config.get("credentials")
    if isinstance(credentials, dict):
        return credentials

    instances = config.get("instances")
    if isinstance(instances, list):
        for instance in instances:
            if not isinstance(instance, dict):
                continue
            instance_credentials = instance.get("credentials")
            if isinstance(instance_credentials, dict):
                return instance_credentials

    return config


def _slack_effective_config(
    *,
    webhook_url: str,
    bot_token: str,
    app_token: str,
    webhook_label: str,
    default_chat_id: str = "",
) -> dict[str, str]:
    """Return the Slack effective config: webhook and/or Socket Mode tokens.

    Empty when nothing is configured. An invalid webhook URL is dropped with a
    static warning naming ``webhook_label`` — Pydantic's ValidationError embeds
    the URL, which carries a secret token in its path, so it is never logged.
    """
    config: dict[str, str] = {}
    if webhook_url:
        try:
            SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
            config["webhook_url"] = webhook_url
        except Exception:
            logger.warning("%s is invalid; skipping Slack webhook", webhook_label)
    if bot_token or app_token:
        config["bot_token"] = bot_token
        config["app_token"] = app_token
    if default_chat_id.strip():
        config["default_chat_id"] = default_chat_id.strip()
    return config


def resolve_effective_integrations(
    *,
    store_integrations: list[dict[str, Any]] | None = None,
    env_integrations: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.opensre and environment variables."""
    store_records = (
        list(store_integrations) if store_integrations is not None else load_integrations()
    )
    env_records = (
        list(env_integrations) if env_integrations is not None else load_env_integrations()
    )
    merged_integrations = merge_local_integrations(store_records, env_records)
    classified_integrations = classify_integrations(merged_integrations)
    source_by_service, store_integration_by_service = _service_metadata(store_records, env_records)

    effective: dict[str, dict[str, Any]] = {}

    for service in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES:
        _publish_classified_effective_service(
            effective,
            classified_integrations,
            source_by_service,
            service,
        )

    if "datadog" not in effective:
        datadog_store_integration = store_integration_by_service.get("datadog")
        if isinstance(datadog_store_integration, dict):
            datadog_credentials = _raw_credentials(datadog_store_integration)
            effective["datadog"] = _effective_entry(
                "local store",
                {
                    "api_key": str(datadog_credentials.get("api_key", "")).strip(),
                    "app_key": str(datadog_credentials.get("app_key", "")).strip(),
                    "site": str(datadog_credentials.get("site", "datadoghq.com")).strip()
                    or "datadoghq.com",
                    "integration_id": str(datadog_store_integration.get("id", "")).strip(),
                },
            )

    tracer_integration = classified_integrations.get("tracer")
    if isinstance(tracer_integration, dict):
        tracer_credentials = _raw_credentials(tracer_integration)
        effective["tracer"] = _effective_entry(
            source_by_service.get("tracer", "local store"),
            {
                "base_url": str(tracer_credentials.get("base_url", "")).strip(),
                "jwt_token": str(tracer_credentials.get("jwt_token", "")).strip(),
            },
        )
    else:
        jwt_token = resolve_env_credential(TRACER_JWT_TOKEN_ENV)
        if jwt_token:
            effective["tracer"] = _effective_entry(
                "local env",
                {
                    "base_url": os.getenv(TRACER_BASE_URL_ENV, "").strip() or get_tracer_base_url(),
                    "jwt_token": jwt_token,
                },
            )

    slack_store_integration = store_integration_by_service.get("slack")
    if isinstance(slack_store_integration, dict):
        slack_credentials = _raw_credentials(slack_store_integration)
        slack_config = _slack_effective_config(
            webhook_url=str(slack_credentials.get("webhook_url", "")).strip(),
            bot_token=str(slack_credentials.get("bot_token", "")).strip(),
            app_token=str(slack_credentials.get("app_token", "")).strip(),
            default_chat_id=str(slack_credentials.get("default_chat_id", "")).strip(),
            webhook_label="Slack webhook URL from store",
        )
        if slack_config:
            effective["slack"] = _effective_entry("local store", slack_config)
    else:
        slack_config = _slack_effective_config(
            webhook_url=os.getenv(SLACK_WEBHOOK_URL_ENV, "").strip(),
            bot_token=resolve_env_credential(SLACK_BOT_TOKEN_ENV),
            app_token=resolve_env_credential(SLACK_APP_TOKEN_ENV),
            default_chat_id=os.getenv(SLACK_DEFAULT_CHAT_ID_ENV, "").strip(),
            webhook_label=SLACK_WEBHOOK_URL_ENV,
        )
        if slack_config:
            effective["slack"] = _effective_entry("local env", slack_config)

    google_docs_integration = classified_integrations.get("google_docs")
    if isinstance(google_docs_integration, dict):
        google_docs_credentials = _raw_credentials(google_docs_integration)
        effective["google_docs"] = _effective_entry(
            source_by_service.get("google_docs", "local env"),
            {
                "credentials_file": str(
                    google_docs_credentials.get("credentials_file", "")
                ).strip(),
                "folder_id": str(google_docs_credentials.get("folder_id", "")).strip(),
            },
        )
    else:
        credentials_file = os.getenv(GOOGLE_CREDENTIALS_FILE_ENV, "").strip()
        folder_id = os.getenv(GOOGLE_DRIVE_FOLDER_ID_ENV, "").strip()
        if credentials_file and folder_id:
            effective["google_docs"] = _effective_entry(
                "local env",
                {
                    "credentials_file": credentials_file,
                    "folder_id": folder_id,
                },
            )

    kafka_integration = classified_integrations.get("kafka")
    if isinstance(kafka_integration, dict):
        kafka_credentials = _raw_credentials(kafka_integration)
        effective["kafka"] = _effective_entry(
            source_by_service.get("kafka", "local env"),
            {
                "bootstrap_servers": str(kafka_credentials.get("bootstrap_servers", "")).strip(),
                "security_protocol": str(
                    kafka_credentials.get("security_protocol", "PLAINTEXT")
                ).strip(),
                "sasl_mechanism": str(kafka_credentials.get("sasl_mechanism", "")).strip(),
                "sasl_username": str(kafka_credentials.get("sasl_username", "")).strip(),
                "sasl_password": str(kafka_credentials.get("sasl_password", "")).strip(),
            },
        )
    else:
        kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
        if kafka_servers:
            effective["kafka"] = _effective_entry(
                "local env",
                {
                    "bootstrap_servers": kafka_servers,
                    "security_protocol": os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip(),
                    "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "").strip(),
                    "sasl_username": os.getenv("KAFKA_SASL_USERNAME", "").strip(),
                    "sasl_password": resolve_env_credential("KAFKA_SASL_PASSWORD"),
                },
            )

    clickhouse_integration = classified_integrations.get("clickhouse")
    if isinstance(clickhouse_integration, dict):
        clickhouse_credentials = _raw_credentials(clickhouse_integration)
        effective["clickhouse"] = _effective_entry(
            source_by_service.get("clickhouse", "local env"),
            {
                "host": str(clickhouse_credentials.get("host", "")).strip(),
                "port": clickhouse_credentials.get("port", 8123),
                "database": str(clickhouse_credentials.get("database", "default")).strip(),
                "username": str(clickhouse_credentials.get("username", "default")).strip(),
                "password": str(clickhouse_credentials.get("password", "")).strip(),
                "secure": clickhouse_credentials.get("secure", False),
            },
        )
    else:
        clickhouse_host = os.getenv("CLICKHOUSE_HOST", "").strip()
        if clickhouse_host:
            effective["clickhouse"] = _effective_entry(
                "local env",
                {
                    "host": clickhouse_host,
                    "port": int(os.getenv("CLICKHOUSE_PORT", "8123") or "8123"),
                    "database": os.getenv("CLICKHOUSE_DATABASE", "default").strip(),
                    "username": os.getenv("CLICKHOUSE_USER", "default").strip(),
                    "password": resolve_env_credential("CLICKHOUSE_PASSWORD"),
                    "secure": os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
                    in ("true", "1", "yes"),
                },
            )

    # Registered plugins have no declared field on the model, so they have to be
    # named here as well; without this the key is dropped as unrecognised one
    # line before the model would have accepted it.
    known_keys = set(EffectiveIntegrations.model_fields) | external_integration_services()
    unknown_keys = set(effective) - known_keys
    if unknown_keys:
        logger.warning(
            "resolve_effective_integrations: dropping unrecognised integration key(s): %s",
            sorted(unknown_keys),
        )
    filtered_effective = {k: v for k, v in effective.items() if k in known_keys}
    return EffectiveIntegrations.model_validate(filtered_effective).model_dump(exclude_none=True)
