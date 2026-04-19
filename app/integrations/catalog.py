"""Shared integration catalog for normalization and resolution."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.config import get_tracer_base_url
from app.integrations.azure_sql import build_azure_sql_config
from app.integrations.github_mcp import build_github_mcp_config
from app.integrations.gitlab import DEFAULT_GITLAB_BASE_URL, build_gitlab_config
from app.integrations.mariadb import build_mariadb_config
from app.integrations.models import (
    AlertmanagerIntegrationConfig,
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    DiscordBotConfig,
    EffectiveIntegrations,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    JiraIntegrationConfig,
    OpsGenieIntegrationConfig,
    SlackWebhookConfig,
)
from app.integrations.mongodb import build_mongodb_config
from app.integrations.mongodb_atlas import build_mongodb_atlas_config
from app.integrations.mysql import build_mysql_config
from app.integrations.openclaw import build_openclaw_config
from app.integrations.postgresql import build_postgresql_config
from app.integrations.sentry import build_sentry_config
from app.integrations.store import _STRUCTURAL_RECORD_FIELDS, load_integrations
from app.services.vercel import VercelConfig

logger = logging.getLogger(__name__)

_SKIP_SERVICES = {"slack"}

_SERVICE_KEY_MAP = {
    "grafana": "grafana",
    "grafana_local": "grafana_local",
    "aws": "aws",
    "eks": "aws",
    "amazon eks": "aws",
    "datadog": "datadog",
    "honeycomb": "honeycomb",
    "coralogix": "coralogix",
    "carologix": "coralogix",
    "github": "github",
    "github_mcp": "github",
    "sentry": "sentry",
    "gitlab": "gitlab",
    "mongodb": "mongodb",
    "mongo": "mongodb",
    "postgresql": "postgresql",
    "postgres": "postgresql",
    "mongodb_atlas": "mongodb_atlas",
    "atlas": "mongodb_atlas",
    "mariadb": "mariadb",
    "vercel": "vercel",
    "opsgenie": "opsgenie",
    "jira": "jira",
    "discord": "discord",
    "openclaw": "openclaw",
    "mysql": "mysql",
    "azure_sql": "azure_sql",
    "bitbucket": "bitbucket",
    "snowflake": "snowflake",
    "azure": "azure",
    "azure monitor": "azure",
    "azure_monitor": "azure",
    "openobserve": "openobserve",
    "open observe": "openobserve",
    "opensearch": "opensearch",
    "open search": "opensearch",
    "alertmanager": "alertmanager",
}


# Services whose classifier emits multiple flat keys but which represent the
# same "family" for multi-instance discovery. For example, Grafana splits into
# ``grafana`` (cloud) and ``grafana_local`` (localhost), but selector helpers
# only look up ``_all_grafana_instances``. Grouping both under the family key
# ensures a hint like ``grafana_instance: "local"`` can find a local instance.
_SERVICE_FAMILY = {
    "grafana": "grafana",
    "grafana_local": "grafana",
}


def _family_key(flat_key: str) -> str:
    return _SERVICE_FAMILY.get(flat_key, flat_key)


def _safe_int(value: Any, default: int) -> int:
    """Coerce ``value`` to int, falling back to ``default`` on bad input.

    Mirrors the helper in ``app.nodes.plan_actions.detect_sources``; duplicated
    here to avoid pulling node-layer code into the shared catalog module.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _record_instances(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a record (v1 or v2 shape) into a list of instance dicts.

    v2 records return their ``instances`` list directly. v1 records are
    migrated on the fly: ``credentials`` plus every non-structural top-level
    field (e.g. AWS ``role_arn``) become the single ``default`` instance's
    credentials. This matches the v1→v2 store migration so downstream
    classification logic reads ONE uniform shape.
    """
    if isinstance(record.get("instances"), list):
        return [
            inst if isinstance(inst, dict) else {} for inst in record["instances"]
        ]
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
    ``app/integrations/selectors.py`` for consumers.
    """
    resolved: dict[str, Any] = {}
    all_instances: dict[str, list[dict[str, Any]]] = {}

    active = [integration for integration in integrations if integration.get("status") == "active"]

    for integration in active:
        service = str(integration.get("service") or "").strip()
        if not service:
            continue

        service_lower = service.lower()
        if service_lower in _SKIP_SERVICES:
            continue

        key = _SERVICE_KEY_MAP.get(service_lower, service_lower)
        record_id = str(integration.get("id", "")).strip()

        for instance in _record_instances(integration):
            credentials = instance.get("credentials", {}) or {}
            instance_name = str(instance.get("name", "default")).strip().lower() or "default"
            instance_tags = instance.get("tags", {}) or {}
            flat_view, flat_key = _classify_service_instance(
                key, credentials, record_id=record_id
            )
            if flat_view is None or flat_key is None:
                continue
            resolved.setdefault(flat_key, flat_view)
            # Bucket under the family key so related classifier outputs (e.g.
            # grafana + grafana_local) share one _all_<family>_instances list.
            all_instances.setdefault(_family_key(flat_key), []).append(
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


def _classify_service_instance(
    key: str, credentials: dict[str, Any], *, record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Classify one instance into (flat_view, resolved_key).

    Returns ``(None, None)`` when the instance is invalid or should be skipped
    (e.g. required field missing). The returned ``resolved_key`` is usually
    ``key`` itself, but Grafana splits into ``grafana`` or ``grafana_local``
    based on its ``is_local`` property.
    """
    if key in ("grafana", "grafana_local"):
        try:
            grafana_config = GrafanaIntegrationConfig.model_validate(
                {
                    "endpoint": credentials.get("endpoint", ""),
                    "api_key": credentials.get("api_key", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if not grafana_config.endpoint:
            return None, None
        if grafana_config.is_local:
            return {
                "endpoint": grafana_config.endpoint,
                "api_key": "",
                "integration_id": grafana_config.integration_id,
            }, "grafana_local"
        if grafana_config.api_key and grafana_config.api_key != "local":
            return grafana_config.model_dump(), "grafana"
        return None, None

    if key == "aws":
        raw_config: dict[str, Any] = {
            "region": credentials.get("region", "us-east-1"),
            "role_arn": credentials.get("role_arn", ""),
            "external_id": credentials.get("external_id", ""),
            "integration_id": record_id,
        }
        if credentials.get("access_key_id") and credentials.get("secret_access_key"):
            raw_config["credentials"] = {
                "access_key_id": credentials.get("access_key_id", ""),
                "secret_access_key": credentials.get("secret_access_key", ""),
                "session_token": credentials.get("session_token", ""),
            }
        try:
            return (
                AWSIntegrationConfig.model_validate(raw_config).model_dump(exclude_none=True),
                "aws",
            )
        except Exception:
            return None, None

    if key == "datadog":
        try:
            datadog_config = DatadogIntegrationConfig.model_validate(
                {
                    "api_key": credentials.get("api_key", ""),
                    "app_key": credentials.get("app_key", ""),
                    "site": credentials.get("site", "datadoghq.com"),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if datadog_config.api_key and datadog_config.app_key:
            return datadog_config.model_dump(), "datadog"
        return None, None

    if key == "honeycomb":
        try:
            honeycomb_config = HoneycombIntegrationConfig.model_validate(
                {
                    "api_key": credentials.get("api_key", ""),
                    "dataset": credentials.get("dataset", ""),
                    "base_url": credentials.get("base_url", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if honeycomb_config.api_key:
            return honeycomb_config.model_dump(), "honeycomb"
        return None, None

    if key == "coralogix":
        try:
            coralogix_config = CoralogixIntegrationConfig.model_validate(
                {
                    "api_key": credentials.get("api_key", ""),
                    "base_url": credentials.get("base_url", ""),
                    "application_name": credentials.get("application_name", ""),
                    "subsystem_name": credentials.get("subsystem_name", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if coralogix_config.api_key:
            return coralogix_config.model_dump(), "coralogix"
        return None, None

    if key == "github":
        try:
            github_config = build_github_mcp_config(
                {
                    "url": credentials.get("url", ""),
                    "mode": credentials.get("mode", "streamable-http"),
                    "command": credentials.get("command", ""),
                    "args": credentials.get("args", []),
                    "auth_token": credentials.get("auth_token", ""),
                    "toolsets": credentials.get("toolsets", []),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        return github_config.model_dump(), "github"

    if key == "sentry":
        try:
            sentry_config = build_sentry_config(
                {
                    "base_url": credentials.get("base_url", "https://sentry.io"),
                    "organization_slug": credentials.get("organization_slug", ""),
                    "auth_token": credentials.get("auth_token", ""),
                    "project_slug": credentials.get("project_slug", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if sentry_config.organization_slug and sentry_config.auth_token:
            return sentry_config.model_dump(), "sentry"
        return None, None

    if key == "gitlab":
        try:
            gitlab_config = build_gitlab_config(
                {
                    "base_url": credentials.get("base_url", ""),
                    "auth_token": credentials.get("auth_token", ""),
                }
            )
        except Exception:
            return None, None
        return gitlab_config.model_dump(), "gitlab"

    if key == "mongodb":
        try:
            mongodb_config = build_mongodb_config(
                {
                    "connection_string": credentials.get("connection_string", ""),
                    "database": credentials.get("database", ""),
                    "auth_source": credentials.get("auth_source", "admin"),
                    "tls": credentials.get("tls", True),
                }
            )
        except Exception:
            return None, None
        if mongodb_config.connection_string:
            return mongodb_config.model_dump(), "mongodb"
        return None, None

    if key == "postgresql":
        try:
            postgresql_config = build_postgresql_config(
                {
                    "host": credentials.get("host", ""),
                    "port": credentials.get("port", 5432),
                    "database": credentials.get("database", ""),
                    "username": credentials.get("username", "postgres"),
                    "password": credentials.get("password", ""),
                    "ssl_mode": credentials.get("ssl_mode", "prefer"),
                }
            )
        except Exception:
            return None, None
        if postgresql_config.host and postgresql_config.database:
            return postgresql_config.model_dump(), "postgresql"
        return None, None

    if key == "mongodb_atlas":
        try:
            atlas_config = build_mongodb_atlas_config(
                {
                    "api_public_key": credentials.get("api_public_key", ""),
                    "api_private_key": credentials.get("api_private_key", ""),
                    "project_id": credentials.get("project_id", ""),
                    "base_url": credentials.get(
                        "base_url", "https://cloud.mongodb.com/api/atlas/v2"
                    ),
                }
            )
        except Exception:
            return None, None
        if (
            atlas_config.api_public_key
            and atlas_config.api_private_key
            and atlas_config.project_id
        ):
            return {
                "api_public_key": atlas_config.api_public_key,
                "api_private_key": atlas_config.api_private_key,
                "project_id": atlas_config.project_id,
                "base_url": atlas_config.base_url,
                "integration_id": record_id,
            }, "mongodb_atlas"
        return None, None

    if key == "mariadb":
        try:
            mariadb_config = build_mariadb_config(
                {
                    "host": credentials.get("host", ""),
                    "port": credentials.get("port", 3306),
                    "database": credentials.get("database", ""),
                    "username": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                    "ssl": credentials.get("ssl", True),
                }
            )
        except Exception:
            return None, None
        if mariadb_config.host and mariadb_config.database:
            return {
                "host": mariadb_config.host,
                "port": mariadb_config.port,
                "database": mariadb_config.database,
                "username": mariadb_config.username,
                "password": mariadb_config.password,
                "ssl": mariadb_config.ssl,
                "integration_id": record_id,
            }, "mariadb"
        return None, None

    if key == "vercel":
        try:
            vercel_config = VercelConfig.model_validate(
                {
                    "api_token": credentials.get("api_token", ""),
                    "team_id": credentials.get("team_id", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if vercel_config.api_token:
            return vercel_config.model_dump(), "vercel"
        return None, None

    if key == "opsgenie":
        try:
            opsgenie_config = OpsGenieIntegrationConfig.model_validate(
                {
                    "api_key": credentials.get("api_key", ""),
                    "region": credentials.get("region", "us"),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if opsgenie_config.api_key:
            return opsgenie_config.model_dump(), "opsgenie"
        return None, None

    if key == "jira":
        try:
            jira_config = JiraIntegrationConfig.model_validate(
                {
                    "base_url": credentials.get("base_url", ""),
                    "email": credentials.get("email", ""),
                    "api_token": credentials.get("api_token", ""),
                    "project_key": credentials.get("project_key", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if jira_config.base_url and jira_config.email and jira_config.api_token:
            return jira_config.model_dump(), "jira"
        return None, None

    if key == "discord":
        try:
            discord_config = DiscordBotConfig.model_validate(
                {
                    "bot_token": credentials.get("bot_token", ""),
                    "application_id": credentials.get("application_id", ""),
                    "public_key": credentials.get("public_key", ""),
                    "default_channel_id": credentials.get("default_channel_id"),
                }
            )
        except Exception:
            return None, None
        if discord_config.bot_token:
            return discord_config.model_dump(), "discord"
        return None, None

    if key == "openclaw":
        try:
            openclaw_config = build_openclaw_config(
                {
                    "url": credentials.get("url", ""),
                    "mode": credentials.get("mode", "streamable-http"),
                    "command": credentials.get("command", ""),
                    "args": credentials.get("args", []),
                    "auth_token": credentials.get("auth_token", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if openclaw_config.is_configured:
            return openclaw_config.model_dump(), "openclaw"
        return None, None

    if key == "mysql":
        try:
            mysql_config = build_mysql_config(
                {
                    "host": credentials.get("host", ""),
                    "port": credentials.get("port", 3306),
                    "database": credentials.get("database", ""),
                    "username": credentials.get("username", "root"),
                    "password": credentials.get("password", ""),
                    "ssl_mode": credentials.get("ssl_mode", "preferred"),
                }
            )
        except Exception:
            return None, None
        if mysql_config.host and mysql_config.database:
            return {
                "host": mysql_config.host,
                "port": mysql_config.port,
                "database": mysql_config.database,
                "username": mysql_config.username,
                "password": mysql_config.password,
                "ssl_mode": mysql_config.ssl_mode,
                "integration_id": record_id,
            }, "mysql"
        return None, None

    if key == "azure_sql":
        try:
            azure_sql_config = build_azure_sql_config(
                {
                    "server": credentials.get("server", ""),
                    "port": credentials.get("port", 1433),
                    "database": credentials.get("database", ""),
                    "username": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                    "driver": credentials.get("driver", "ODBC Driver 18 for SQL Server"),
                    "encrypt": credentials.get("encrypt", True),
                }
            )
        except Exception:
            return None, None
        if azure_sql_config.server and azure_sql_config.database:
            return azure_sql_config.model_dump(), "azure_sql"
        return None, None

    if key == "alertmanager":
        try:
            alertmanager_config = AlertmanagerIntegrationConfig.model_validate(
                {
                    "base_url": credentials.get("base_url", ""),
                    "bearer_token": credentials.get("bearer_token", ""),
                    "username": credentials.get("username", ""),
                    "password": credentials.get("password", ""),
                    "integration_id": record_id,
                }
            )
        except Exception:
            return None, None
        if alertmanager_config.base_url:
            return alertmanager_config.model_dump(), "alertmanager"
        return None, None

    if key == "bitbucket":
        workspace = str(credentials.get("workspace", "")).strip()
        if not workspace:
            return None, None
        base_url = str(
            credentials.get("base_url", "https://api.bitbucket.org/2.0")
        ).strip() or "https://api.bitbucket.org/2.0"
        return {
            "workspace": workspace,
            "username": str(credentials.get("username", "")).strip(),
            "app_password": str(credentials.get("app_password", "")).strip(),
            "base_url": base_url,
            "max_results": max(1, min(_safe_int(credentials.get("max_results", 25), 25), 100)),
            "integration_id": record_id,
        }, "bitbucket"

    if key == "snowflake":
        account_identifier = str(
            credentials.get("account_identifier", credentials.get("account", ""))
        ).strip()
        token = str(credentials.get("token", "")).strip()
        if not (account_identifier and token):
            return None, None
        return {
            "account_identifier": account_identifier,
            "user": str(credentials.get("user", "")).strip(),
            "password": str(credentials.get("password", "")).strip(),
            "token": token,
            "warehouse": str(credentials.get("warehouse", "")).strip(),
            "role": str(credentials.get("role", "")).strip(),
            "database": str(credentials.get("database", "")).strip(),
            "schema": str(credentials.get("schema", "")).strip(),
            "max_results": max(1, min(_safe_int(credentials.get("max_results", 50), 50), 200)),
            "integration_id": record_id,
        }, "snowflake"

    if key == "azure":
        workspace_id = str(credentials.get("workspace_id", "")).strip()
        access_token = str(credentials.get("access_token", "")).strip()
        if not (workspace_id and access_token):
            return None, None
        endpoint = str(
            credentials.get("endpoint", "https://api.loganalytics.io")
        ).strip() or "https://api.loganalytics.io"
        return {
            "workspace_id": workspace_id,
            "access_token": access_token,
            "endpoint": endpoint,
            "tenant_id": str(credentials.get("tenant_id", "")).strip(),
            "subscription_id": str(credentials.get("subscription_id", "")).strip(),
            "max_results": max(1, min(_safe_int(credentials.get("max_results", 100), 100), 500)),
            "integration_id": record_id,
        }, "azure"

    if key == "openobserve":
        base_url = str(credentials.get("base_url", "")).strip()
        api_token = str(credentials.get("api_token", "")).strip()
        username = str(credentials.get("username", "")).strip()
        password = str(credentials.get("password", "")).strip()
        if not (base_url and (api_token or (username and password))):
            return None, None
        return {
            "base_url": base_url.rstrip("/"),
            "org": str(credentials.get("org", "default")).strip() or "default",
            "api_token": api_token,
            "username": username,
            "password": password,
            "stream": str(credentials.get("stream", "")).strip(),
            "max_results": max(1, min(_safe_int(credentials.get("max_results", 100), 100), 500)),
            "integration_id": record_id,
        }, "openobserve"

    if key == "opensearch":
        url = str(credentials.get("url", "")).strip()
        if not url:
            return None, None
        return {
            "url": url.rstrip("/"),
            "api_key": str(credentials.get("api_key", "")).strip(),
            "index_pattern": str(credentials.get("index_pattern", "*")).strip() or "*",
            "max_results": max(1, min(_safe_int(credentials.get("max_results", 100), 100), 500)),
            "integration_id": record_id,
        }, "opensearch"

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
            "%s is not valid JSON (parse failed at line %d col %d); "
            "falling back to legacy vars",
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


def load_env_integrations() -> list[dict[str, Any]]:
    """Build integration records from local environment variables."""
    integrations: list[dict[str, Any]] = []

    grafana_multi = _parse_instances_env("GRAFANA_INSTANCES", "grafana")
    if grafana_multi is not None:
        integrations.append(grafana_multi)
        grafana_endpoint = ""
        grafana_api_key = ""
    else:
        grafana_endpoint = os.getenv("GRAFANA_INSTANCE_URL", "").strip()
        grafana_api_key = os.getenv("GRAFANA_READ_TOKEN", "").strip()
    if grafana_endpoint and grafana_api_key:
        grafana_config = GrafanaIntegrationConfig.model_validate(
            {
                "endpoint": grafana_endpoint,
                "api_key": grafana_api_key,
            }
        )
        integrations.append(
            {
                "id": "env-grafana",
                "service": "grafana",
                "status": "active",
                "credentials": {
                    "endpoint": grafana_config.endpoint,
                    "api_key": grafana_config.api_key,
                },
            }
        )

    datadog_multi = _parse_instances_env("DD_INSTANCES", "datadog")
    if datadog_multi is not None:
        integrations.append(datadog_multi)
        datadog_api_key = ""
        datadog_app_key = ""
        datadog_site = ""
    else:
        datadog_api_key = os.getenv("DD_API_KEY", "").strip()
        datadog_app_key = os.getenv("DD_APP_KEY", "").strip()
        datadog_site = os.getenv("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
    if datadog_api_key and datadog_app_key:
        datadog_config = DatadogIntegrationConfig.model_validate(
            {
                "api_key": datadog_api_key,
                "app_key": datadog_app_key,
                "site": datadog_site,
            }
        )
        integrations.append(
            {
                "id": "env-datadog",
                "service": "datadog",
                "status": "active",
                "credentials": datadog_config.model_dump(exclude={"integration_id"}),
            }
        )

    honeycomb_multi = _parse_instances_env("HONEYCOMB_INSTANCES", "honeycomb")
    if honeycomb_multi is not None:
        integrations.append(honeycomb_multi)
        honeycomb_api_key = ""
    else:
        honeycomb_api_key = os.getenv("HONEYCOMB_API_KEY", "").strip()
    if honeycomb_api_key:
        honeycomb_config = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": honeycomb_api_key,
                "dataset": os.getenv("HONEYCOMB_DATASET", "").strip(),
                "base_url": os.getenv("HONEYCOMB_API_URL", "").strip(),
            }
        )
        integrations.append(
            {
                "id": "env-honeycomb",
                "service": "honeycomb",
                "status": "active",
                "credentials": honeycomb_config.model_dump(exclude={"integration_id"}),
            }
        )

    coralogix_multi = _parse_instances_env("CORALOGIX_INSTANCES", "coralogix")
    if coralogix_multi is not None:
        integrations.append(coralogix_multi)
        coralogix_api_key = ""
    else:
        coralogix_api_key = os.getenv("CORALOGIX_API_KEY", "").strip()
    if coralogix_api_key:
        coralogix_config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": coralogix_api_key,
                "base_url": os.getenv("CORALOGIX_API_URL", "").strip(),
                "application_name": os.getenv("CORALOGIX_APPLICATION_NAME", "").strip(),
                "subsystem_name": os.getenv("CORALOGIX_SUBSYSTEM_NAME", "").strip(),
            }
        )
        integrations.append(
            {
                "id": "env-coralogix",
                "service": "coralogix",
                "status": "active",
                "credentials": coralogix_config.model_dump(exclude={"integration_id"}),
            }
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
        aws_role_arn = os.getenv("AWS_ROLE_ARN", "").strip()
        aws_external_id = os.getenv("AWS_EXTERNAL_ID", "").strip()
        aws_region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        aws_session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()
    if aws_role_arn:
        aws_config = AWSIntegrationConfig.model_validate(
            {
                "role_arn": aws_role_arn,
                "external_id": aws_external_id,
                "region": aws_region,
            }
        )
        integrations.append(
            {
                "id": "env-aws",
                "service": "aws",
                "status": "active",
                "role_arn": aws_config.role_arn,
                "external_id": aws_config.external_id,
                "credentials": {"region": aws_config.region},
            }
        )
    elif aws_access_key_id and aws_secret_access_key:
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
        aws_credentials = aws_config.credentials
        if aws_credentials is not None:
            integrations.append(
                {
                    "id": "env-aws",
                    "service": "aws",
                    "status": "active",
                    "credentials": {
                        "access_key_id": aws_credentials.access_key_id,
                        "secret_access_key": aws_credentials.secret_access_key,
                        "session_token": aws_credentials.session_token,
                        "region": aws_config.region,
                    },
                }
            )

    github_mode = os.getenv("GITHUB_MCP_MODE", "streamable-http").strip() or "streamable-http"
    github_url = os.getenv("GITHUB_MCP_URL", "").strip()
    github_command = os.getenv("GITHUB_MCP_COMMAND", "").strip()
    github_args = os.getenv("GITHUB_MCP_ARGS", "").strip()
    github_auth_token = os.getenv("GITHUB_MCP_AUTH_TOKEN", "").strip()
    github_toolsets = os.getenv("GITHUB_MCP_TOOLSETS", "").strip()
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
            {
                "id": "env-github",
                "service": "github",
                "status": "active",
                "credentials": github_config.model_dump(exclude={"integration_id"}),
            }
        )

    sentry_org_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    sentry_auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if sentry_org_slug and sentry_auth_token:
        sentry_config = build_sentry_config(
            {
                "base_url": os.getenv("SENTRY_URL", "https://sentry.io").strip()
                or "https://sentry.io",
                "organization_slug": sentry_org_slug,
                "auth_token": sentry_auth_token,
                "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
            }
        )
        integrations.append(
            {
                "id": "env-sentry",
                "service": "sentry",
                "status": "active",
                "credentials": sentry_config.model_dump(exclude={"integration_id"}),
            }
        )

    gitlab_access_token = os.getenv("GITLAB_ACCESS_TOKEN", "").strip()
    if gitlab_access_token:
        gitlab_config = build_gitlab_config(
            {
                "base_url": os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip()
                or DEFAULT_GITLAB_BASE_URL,
                "auth_token": gitlab_access_token,
            }
        )
        integrations.append(
            {
                "id": "env-gitlab",
                "service": "gitlab",
                "status": "active",
                "credentials": gitlab_config.model_dump(),
            }
        )

    mongodb_connection_string = os.getenv("MONGODB_CONNECTION_STRING", "").strip()
    if mongodb_connection_string:
        mongodb_config = build_mongodb_config(
            {
                "connection_string": mongodb_connection_string,
                "database": os.getenv("MONGODB_DATABASE", "").strip(),
                "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin").strip() or "admin",
                "tls": os.getenv("MONGODB_TLS", "true").strip().lower() in ("true", "1", "yes"),
            }
        )
        integrations.append(
            {
                "id": "env-mongodb",
                "service": "mongodb",
                "status": "active",
                "credentials": mongodb_config.model_dump(exclude={"integration_id"}),
            }
        )

    postgresql_host = os.getenv("POSTGRESQL_HOST", "").strip()
    postgresql_database = os.getenv("POSTGRESQL_DATABASE", "").strip()
    if postgresql_host and postgresql_database:
        postgresql_config = build_postgresql_config(
            {
                "host": postgresql_host,
                "port": int(_pg_port)
                if (_pg_port := os.getenv("POSTGRESQL_PORT", "").strip()) and _pg_port.isdigit()
                else 5432,
                "database": postgresql_database,
                "username": os.getenv("POSTGRESQL_USERNAME", "postgres").strip() or "postgres",
                "password": os.getenv("POSTGRESQL_PASSWORD", "").strip(),
                "ssl_mode": os.getenv("POSTGRESQL_SSL_MODE", "prefer").strip() or "prefer",
            }
        )
        integrations.append(
            {
                "id": "env-postgresql",
                "service": "postgresql",
                "status": "active",
                "credentials": postgresql_config.model_dump(exclude={"integration_id"}),
            }
        )

    vercel_api_token = os.getenv("VERCEL_API_TOKEN", "").strip()
    if vercel_api_token:
        vercel_config = VercelConfig.model_validate(
            {
                "api_token": vercel_api_token,
                "team_id": os.getenv("VERCEL_TEAM_ID", "").strip(),
            }
        )
        integrations.append(
            {
                "id": "env-vercel",
                "service": "vercel",
                "status": "active",
                "credentials": vercel_config.model_dump(exclude={"integration_id"}),
            }
        )

    opsgenie_api_key = os.getenv("OPSGENIE_API_KEY", "").strip()
    if opsgenie_api_key:
        opsgenie_config = OpsGenieIntegrationConfig.model_validate(
            {
                "api_key": opsgenie_api_key,
                "region": os.getenv("OPSGENIE_REGION", "us").strip() or "us",
            }
        )
        integrations.append(
            {
                "id": "env-opsgenie",
                "service": "opsgenie",
                "status": "active",
                "credentials": opsgenie_config.model_dump(exclude={"integration_id"}),
            }
        )

    jira_base_url = os.getenv("JIRA_BASE_URL", "").strip()
    jira_email = os.getenv("JIRA_EMAIL", "").strip()
    jira_api_token = os.getenv("JIRA_API_TOKEN", "").strip()
    jira_project_key = os.getenv("JIRA_PROJECT_KEY", "").strip()
    if jira_base_url and jira_email and jira_api_token:
        jira_config = JiraIntegrationConfig.model_validate(
            {
                "base_url": jira_base_url,
                "email": jira_email,
                "api_token": jira_api_token,
                "project_key": jira_project_key,
            }
        )
        integrations.append(
            {
                "id": "env-jira",
                "service": "jira",
                "status": "active",
                "credentials": jira_config.model_dump(exclude={"integration_id"}),
            }
        )

    discord_bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if discord_bot_token:
        discord_config = DiscordBotConfig.model_validate(
            {
                "bot_token": discord_bot_token,
                "application_id": os.getenv("DISCORD_APPLICATION_ID", "").strip(),
                "public_key": os.getenv("DISCORD_PUBLIC_KEY", "").strip(),
                "default_channel_id": os.getenv("DISCORD_DEFAULT_CHANNEL_ID", "").strip() or None,
            }
        )
        integrations.append(
            {
                "id": "env-discord",
                "service": "discord",
                "status": "active",
                "credentials": discord_config.model_dump(),
            }
        )

    atlas_pub = os.getenv("MONGODB_ATLAS_PUBLIC_KEY", "").strip()
    atlas_priv = os.getenv("MONGODB_ATLAS_PRIVATE_KEY", "").strip()
    atlas_project = os.getenv("MONGODB_ATLAS_PROJECT_ID", "").strip()
    if atlas_pub and atlas_priv and atlas_project:
        atlas_config = build_mongodb_atlas_config(
            {
                "api_public_key": atlas_pub,
                "api_private_key": atlas_priv,
                "project_id": atlas_project,
                "base_url": os.getenv(
                    "MONGODB_ATLAS_BASE_URL", "https://cloud.mongodb.com/api/atlas/v2"
                ).strip(),
            }
        )
        integrations.append(
            {
                "id": "env-mongodb-atlas",
                "service": "mongodb_atlas",
                "status": "active",
                "credentials": atlas_config.model_dump(exclude={"integration_id"}),
            }
        )

    openclaw_url = os.getenv("OPENCLAW_MCP_URL", "").strip()
    openclaw_command = os.getenv("OPENCLAW_MCP_COMMAND", "").strip()
    openclaw_mode = os.getenv("OPENCLAW_MCP_MODE", "streamable-http").strip().lower()
    openclaw_mode = openclaw_mode or "streamable-http"
    if (openclaw_mode == "stdio" and openclaw_command) or (
        openclaw_mode != "stdio" and openclaw_url
    ):
        try:
            openclaw_config = build_openclaw_config(
                {
                    "url": openclaw_url,
                    "mode": openclaw_mode,
                    "command": openclaw_command,
                    "args": [
                        part for part in os.getenv("OPENCLAW_MCP_ARGS", "").strip().split() if part
                    ],
                    "auth_token": os.getenv("OPENCLAW_MCP_AUTH_TOKEN", "").strip(),
                }
            )
            integrations.append(
                {
                    "id": "env-openclaw",
                    "service": "openclaw",
                    "status": "active",
                    "credentials": openclaw_config.model_dump(exclude={"integration_id"}),
                }
            )
        except Exception:
            logger.debug("Failed to load OpenClaw config from env", exc_info=True)

    mariadb_host = os.getenv("MARIADB_HOST", "").strip()
    mariadb_database = os.getenv("MARIADB_DATABASE", "").strip()
    if mariadb_host and mariadb_database:
        try:
            mariadb_config = build_mariadb_config(
                {
                    "host": mariadb_host,
                    "port": os.getenv("MARIADB_PORT", "3306").strip(),
                    "database": mariadb_database,
                    "username": os.getenv("MARIADB_USERNAME", "").strip(),
                    "password": os.getenv("MARIADB_PASSWORD", "").strip(),
                    "ssl": os.getenv("MARIADB_SSL", "true").strip().lower() in ("true", "1", "yes"),
                }
            )
            integrations.append(
                {
                    "id": "env-mariadb",
                    "service": "mariadb",
                    "status": "active",
                    "credentials": mariadb_config.model_dump(exclude={"integration_id"}),
                }
            )
        except Exception:
            logger.debug("Failed to load MariaDB config from env", exc_info=True)

    mysql_host = os.getenv("MYSQL_HOST", "").strip()
    mysql_database = os.getenv("MYSQL_DATABASE", "").strip()
    if mysql_host and mysql_database:
        mysql_config = build_mysql_config(
            {
                "host": mysql_host,
                "port": int(_mysql_port)
                if (_mysql_port := os.getenv("MYSQL_PORT", "").strip()) and _mysql_port.isdigit()
                else 3306,
                "database": mysql_database,
                "username": os.getenv("MYSQL_USERNAME", "root").strip() or "root",
                "password": os.getenv("MYSQL_PASSWORD", "").strip(),
                "ssl_mode": os.getenv("MYSQL_SSL_MODE", "preferred").strip() or "preferred",
            }
        )
        integrations.append(
            {
                "id": "env-mysql",
                "service": "mysql",
                "status": "active",
                "credentials": mysql_config.model_dump(exclude={"integration_id"}),
            }
        )

    azure_sql_server = os.getenv("AZURE_SQL_SERVER", "").strip()
    azure_sql_database = os.getenv("AZURE_SQL_DATABASE", "").strip()
    if azure_sql_server and azure_sql_database:
        _az_port = os.getenv("AZURE_SQL_PORT", "").strip()
        azure_sql_config = build_azure_sql_config(
            {
                "server": azure_sql_server,
                "port": int(_az_port) if _az_port and _az_port.isdigit() else 1433,
                "database": azure_sql_database,
                "username": os.getenv("AZURE_SQL_USERNAME", "").strip(),
                "password": os.getenv("AZURE_SQL_PASSWORD", "").strip(),
                "driver": os.getenv("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server").strip(),
                "encrypt": os.getenv("AZURE_SQL_ENCRYPT", "true").strip().lower()
                in ("true", "1", "yes"),
            }
        )
        integrations.append(
            {
                "id": "env-azure-sql",
                "service": "azure_sql",
                "status": "active",
                "credentials": azure_sql_config.model_dump(exclude={"integration_id"}),
            }
        )

    bitbucket_workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    if bitbucket_workspace:
        integrations.append(
            {
                "id": "env-bitbucket",
                "service": "bitbucket",
                "status": "active",
                "credentials": {
                    "workspace": bitbucket_workspace,
                    "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
                    "app_password": os.getenv("BITBUCKET_APP_PASSWORD", "").strip(),
                    "base_url": os.getenv(
                        "BITBUCKET_BASE_URL", "https://api.bitbucket.org/2.0"
                    ).strip()
                    or "https://api.bitbucket.org/2.0",
                    "max_results": _safe_int(os.getenv("BITBUCKET_MAX_RESULTS", "25"), 25),
                },
            }
        )

    snowflake_account = (
        os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "").strip()
        or os.getenv("SNOWFLAKE_ACCOUNT", "").strip()
    )
    snowflake_token = os.getenv("SNOWFLAKE_TOKEN", "").strip()
    if snowflake_account and snowflake_token:
        integrations.append(
            {
                "id": "env-snowflake",
                "service": "snowflake",
                "status": "active",
                "credentials": {
                    "account_identifier": snowflake_account,
                    "user": os.getenv("SNOWFLAKE_USER", "").strip(),
                    "password": os.getenv("SNOWFLAKE_PASSWORD", "").strip(),
                    "token": snowflake_token,
                    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "").strip(),
                    "role": os.getenv("SNOWFLAKE_ROLE", "").strip(),
                    "database": os.getenv("SNOWFLAKE_DATABASE", "").strip(),
                    "schema": os.getenv("SNOWFLAKE_SCHEMA", "").strip(),
                    "max_results": _safe_int(os.getenv("SNOWFLAKE_MAX_RESULTS", "50"), 50),
                },
            }
        )

    azure_workspace_id = os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    azure_access_token = os.getenv("AZURE_LOG_ANALYTICS_TOKEN", "").strip()
    if azure_workspace_id and azure_access_token:
        integrations.append(
            {
                "id": "env-azure",
                "service": "azure",
                "status": "active",
                "credentials": {
                    "workspace_id": azure_workspace_id,
                    "access_token": azure_access_token,
                    "endpoint": (
                        os.getenv(
                            "AZURE_LOG_ANALYTICS_ENDPOINT", "https://api.loganalytics.io"
                        ).strip()
                        or "https://api.loganalytics.io"
                    ),
                    "tenant_id": os.getenv("AZURE_TENANT_ID", "").strip(),
                    "subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", "").strip(),
                    "max_results": _safe_int(os.getenv("AZURE_MAX_RESULTS", "100"), 100),
                },
            }
        )

    openobserve_url = os.getenv("OPENOBSERVE_URL", "").strip()
    openobserve_token = os.getenv("OPENOBSERVE_TOKEN", "").strip()
    openobserve_username = os.getenv("OPENOBSERVE_USERNAME", "").strip()
    openobserve_password = os.getenv("OPENOBSERVE_PASSWORD", "").strip()
    if openobserve_url and (openobserve_token or (openobserve_username and openobserve_password)):
        integrations.append(
            {
                "id": "env-openobserve",
                "service": "openobserve",
                "status": "active",
                "credentials": {
                    "base_url": openobserve_url.rstrip("/"),
                    "org": os.getenv("OPENOBSERVE_ORG", "default").strip() or "default",
                    "api_token": openobserve_token,
                    "username": openobserve_username,
                    "password": openobserve_password,
                    "stream": os.getenv("OPENOBSERVE_STREAM", "").strip(),
                    "max_results": _safe_int(os.getenv("OPENOBSERVE_MAX_RESULTS", "100"), 100),
                },
            }
        )

    opensearch_url = os.getenv("OPENSEARCH_URL", "").strip()
    if opensearch_url:
        integrations.append(
            {
                "id": "env-opensearch",
                "service": "opensearch",
                "status": "active",
                "credentials": {
                    "url": opensearch_url.rstrip("/"),
                    "api_key": os.getenv("OPENSEARCH_API_KEY", "").strip(),
                    "index_pattern": os.getenv("OPENSEARCH_INDEX_PATTERN", "*").strip() or "*",
                    "max_results": _safe_int(os.getenv("OPENSEARCH_MAX_RESULTS", "100"), 100),
                },
            }
        )

    alertmanager_url = os.getenv("ALERTMANAGER_URL", "").strip().rstrip("/")
    if alertmanager_url:
        try:
            alertmanager_config = AlertmanagerIntegrationConfig.model_validate(
                {
                    "base_url": alertmanager_url,
                    "bearer_token": os.getenv("ALERTMANAGER_BEARER_TOKEN", "").strip(),
                    "username": os.getenv("ALERTMANAGER_USERNAME", "").strip(),
                    "password": os.getenv("ALERTMANAGER_PASSWORD", "").strip(),
                }
            )
            integrations.append(
                {
                    "id": "env-alertmanager",
                    "service": "alertmanager",
                    "status": "active",
                    "credentials": alertmanager_config.model_dump(exclude={"integration_id"}),
                }
            )
        except Exception:
            logger.debug("Failed to load Alertmanager config from env", exc_info=True)

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
    raw_credentials = config.get("credentials", config)
    return raw_credentials if isinstance(raw_credentials, dict) else {}


def resolve_effective_integrations(
    *,
    store_integrations: list[dict[str, Any]] | None = None,
    env_integrations: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.tracer and environment variables."""
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

    direct_services = (
        "grafana",
        "aws",
        "datadog",
        "honeycomb",
        "coralogix",
        "github",
        "sentry",
        "gitlab",
        "mongodb",
        "postgresql",
        "mongodb_atlas",
        "mariadb",
        "vercel",
        "opsgenie",
        "jira",
        "discord",
        "openclaw",
        "mysql",
        "azure_sql",
        "snowflake",
        "azure",
        "openobserve",
        "opensearch",
        "alertmanager",
    )
    for service in direct_services:
        resolved_integration = classified_integrations.get(service)
        if isinstance(resolved_integration, dict):
            effective[service] = _effective_entry(
                source_by_service.get(service, "local env"),
                resolved_integration,
            )
            all_instances = classified_integrations.get(f"_all_{service}_instances")
            # Mirror the publication condition used by classify_integrations:
            # sibling key is emitted when there is more than one instance OR
            # when a single instance has a non-default name. Both cases are
            # user-meaningful and should propagate to the effective view.
            if isinstance(all_instances, list) and all_instances and (
                len(all_instances) > 1
                or str(all_instances[0].get("name", "default")) != "default"
            ):
                effective[service]["instances"] = all_instances

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
        jwt_token = os.getenv("JWT_TOKEN", "").strip()
        if jwt_token:
            effective["tracer"] = _effective_entry(
                "local env",
                {
                    "base_url": os.getenv("TRACER_API_URL", "").strip() or get_tracer_base_url(),
                    "jwt_token": jwt_token,
                },
            )

    slack_store_integration = store_integration_by_service.get("slack")
    if isinstance(slack_store_integration, dict):
        slack_credentials = _raw_credentials(slack_store_integration)
        webhook_url = str(slack_credentials.get("webhook_url", "")).strip()
        if webhook_url:
            slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
            effective["slack"] = _effective_entry("local store", slack_config.model_dump())
    elif slack_webhook_url := os.getenv("SLACK_WEBHOOK_URL", "").strip():
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": slack_webhook_url})
        effective["slack"] = _effective_entry("local env", slack_config.model_dump())

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
        credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
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
                    "sasl_password": os.getenv("KAFKA_SASL_PASSWORD", "").strip(),
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
                    "password": os.getenv("CLICKHOUSE_PASSWORD", "").strip(),
                    "secure": os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
                    in ("true", "1", "yes"),
                },
            )

    return EffectiveIntegrations.model_validate(effective).model_dump(exclude_none=True)
