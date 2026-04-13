"""Verification helpers for local and env-backed integration setup."""

from __future__ import annotations

import os
from typing import Any

import boto3
import httpx
import requests

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.config import get_tracer_base_url
from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.mariadb import build_mariadb_config, validate_mariadb_config
from app.integrations.models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    EffectiveIntegrations,
    GoogleDocsIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
    TracerIntegrationConfig,
)
from app.integrations.mongodb import build_mongodb_config, validate_mongodb_config
from app.integrations.mongodb_atlas import build_mongodb_atlas_config, validate_mongodb_atlas_config
from app.integrations.postgresql import build_postgresql_config, validate_postgresql_config
from app.integrations.sentry import build_sentry_config, validate_sentry_config
from app.integrations.store import load_integrations
from app.nodes.resolve_integrations.node import (
    _classify_integrations,
    _load_env_integrations,
    _merge_local_integrations,
)
from app.services.coralogix import CoralogixClient
from app.services.datadog.client import DatadogClient, DatadogConfig
from app.services.honeycomb import HoneycombClient
from app.services.opsgenie import OpsGenieClient, OpsGenieConfig
from app.services.tracer_client.client import TracerClient
from app.services.vercel.client import VercelClient, VercelConfig

SUPPORTED_VERIFY_SERVICES = (
    "grafana",
    "datadog",
    "honeycomb",
    "coralogix",
    "aws",
    "slack",
    "tracer",
    "github",
    "sentry",
    "mongodb",
    "postgresql",
    "mongodb_atlas",
    "mariadb",
    "google_docs",
    "vercel",
    "opsgenie",
    "kafka",
    "clickhouse",
    "bitbucket",
    "discord",
)
CORE_VERIFY_SERVICES = frozenset({"grafana", "datadog", "honeycomb", "coralogix", "aws"})
_SUPPORTED_GRAFANA_TYPES = ("loki", "tempo", "prometheus")


def _result(
    service: str,
    source: str,
    status: str,
    detail: str,
) -> dict[str, str]:
    return {
        "service": service,
        "source": source,
        "status": status,
        "detail": detail,
    }


def resolve_effective_integrations() -> dict[str, dict[str, Any]]:
    """Resolve effective local integrations from ~/.tracer and env vars."""
    store_integrations = load_integrations()
    env_integrations = _load_env_integrations()
    merged_integrations = _merge_local_integrations(store_integrations, env_integrations)
    classified_integrations = _classify_integrations(merged_integrations)

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

    effective: dict[str, dict[str, Any]] = {}
    for service in CORE_VERIFY_SERVICES:
        resolved_integration = classified_integrations.get(service)
        if isinstance(resolved_integration, dict):
            effective[service] = {
                "source": source_by_service.get(service, "local env"),
                "config": resolved_integration,
            }

    if "datadog" not in effective:
        datadog_store_integration = store_integration_by_service.get("datadog")
        if isinstance(datadog_store_integration, dict):
            datadog_credentials = datadog_store_integration.get("credentials", {})
            if isinstance(datadog_credentials, dict):
                effective["datadog"] = {
                    "source": "local store",
                    "config": {
                        "api_key": str(datadog_credentials.get("api_key", "")).strip(),
                        "app_key": str(datadog_credentials.get("app_key", "")).strip(),
                        "site": str(datadog_credentials.get("site", "datadoghq.com")).strip()
                        or "datadoghq.com",
                        "integration_id": str(datadog_store_integration.get("id", "")).strip(),
                    },
                }

    honeycomb_integration = classified_integrations.get("honeycomb")
    if isinstance(honeycomb_integration, dict):
        effective["honeycomb"] = {
            "source": source_by_service.get("honeycomb", "local env"),
            "config": {
                "api_key": str(honeycomb_integration.get("api_key", "")).strip(),
                "dataset": str(honeycomb_integration.get("dataset", "")).strip(),
                "base_url": str(honeycomb_integration.get("base_url", "")).strip(),
            },
        }

    coralogix_integration = classified_integrations.get("coralogix")
    if isinstance(coralogix_integration, dict):
        effective["coralogix"] = {
            "source": source_by_service.get("coralogix", "local env"),
            "config": {
                "api_key": str(coralogix_integration.get("api_key", "")).strip(),
                "base_url": str(coralogix_integration.get("base_url", "")).strip(),
                "application_name": str(coralogix_integration.get("application_name", "")).strip(),
                "subsystem_name": str(coralogix_integration.get("subsystem_name", "")).strip(),
            },
        }

    tracer_integration = classified_integrations.get("tracer")
    if isinstance(tracer_integration, dict):
        tracer_credentials = tracer_integration.get("credentials", {})
        effective["tracer"] = {
            "source": source_by_service.get("tracer", "local store"),
            "config": {
                "base_url": str(tracer_credentials.get("base_url", "")).strip(),
                "jwt_token": str(tracer_credentials.get("jwt_token", "")).strip(),
            },
        }
    else:
        jwt_token = os.getenv("JWT_TOKEN", "").strip()
        if jwt_token:
            effective["tracer"] = {
                "source": "local env",
                "config": {
                    "base_url": os.getenv("TRACER_API_URL", "").strip() or get_tracer_base_url(),
                    "jwt_token": jwt_token,
                },
            }

    slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if slack_webhook_url:
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": slack_webhook_url})
        effective["slack"] = {
            "source": "local env",
            "config": slack_config.model_dump(),
        }

    github_integration = classified_integrations.get("github")
    if isinstance(github_integration, dict):
        effective["github"] = {
            "source": source_by_service.get("github", "local env"),
            "config": {
                "url": str(github_integration.get("url", "")).strip(),
                "mode": str(github_integration.get("mode", "streamable-http")).strip(),
                "command": str(github_integration.get("command", "")).strip(),
                "args": github_integration.get("args", []),
                "auth_token": str(github_integration.get("auth_token", "")).strip(),
                "toolsets": github_integration.get("toolsets", []),
            },
        }

    sentry_integration = classified_integrations.get("sentry")
    if isinstance(sentry_integration, dict):
        effective["sentry"] = {
            "source": source_by_service.get("sentry", "local env"),
            "config": {
                "base_url": str(sentry_integration.get("base_url", "")).strip(),
                "organization_slug": str(sentry_integration.get("organization_slug", "")).strip(),
                "auth_token": str(sentry_integration.get("auth_token", "")).strip(),
                "project_slug": str(sentry_integration.get("project_slug", "")).strip(),
            },
        }

    mongodb_integration = classified_integrations.get("mongodb")
    if isinstance(mongodb_integration, dict):
        effective["mongodb"] = {
            "source": source_by_service.get("mongodb", "local env"),
            "config": {
                "connection_string": str(mongodb_integration.get("connection_string", "")).strip(),
                "database": str(mongodb_integration.get("database", "")).strip(),
                "auth_source": str(mongodb_integration.get("auth_source", "admin")).strip(),
                "tls": mongodb_integration.get("tls", True),
            },
        }
    else:
        # Check env vars
        mongodb_conn = os.getenv("MONGODB_CONNECTION_STRING", "").strip()
        if mongodb_conn:
            effective["mongodb"] = {
                "source": "local env",
                "config": {
                    "connection_string": mongodb_conn,
                    "database": os.getenv("MONGODB_DATABASE", "").strip(),
                    "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin").strip() or "admin",
                    "tls": os.getenv("MONGODB_TLS", "true").strip().lower() in ("true", "1", "yes"),
                },
            }

    postgresql_integration = classified_integrations.get("postgresql")
    if isinstance(postgresql_integration, dict):
        effective["postgresql"] = {
            "source": source_by_service.get("postgresql", "local env"),
            "config": postgresql_integration,
        }
    else:
        pg_host = os.getenv("POSTGRESQL_HOST", "").strip()
        pg_database = os.getenv("POSTGRESQL_DATABASE", "").strip()
        if pg_host and pg_database:
            _pg_port = os.getenv("POSTGRESQL_PORT", "").strip()
            effective["postgresql"] = {
                "source": "local env",
                "config": {
                    "host": pg_host,
                    "port": int(_pg_port) if _pg_port.isdigit() else 5432,
                    "database": pg_database,
                    "username": os.getenv("POSTGRESQL_USERNAME", "postgres").strip() or "postgres",
                    "password": os.getenv("POSTGRESQL_PASSWORD", "").strip(),
                    "ssl_mode": os.getenv("POSTGRESQL_SSL_MODE", "prefer").strip() or "prefer",
                },
            }

    mongodb_atlas_integration = classified_integrations.get("mongodb_atlas")
    if isinstance(mongodb_atlas_integration, dict):
        effective["mongodb_atlas"] = {
            "source": source_by_service.get("mongodb_atlas", "local env"),
            "config": {
                "api_public_key": str(mongodb_atlas_integration.get("api_public_key", "")).strip(),
                "api_private_key": str(
                    mongodb_atlas_integration.get("api_private_key", "")
                ).strip(),
                "project_id": str(mongodb_atlas_integration.get("project_id", "")).strip(),
                "base_url": str(
                    mongodb_atlas_integration.get(
                        "base_url", "https://cloud.mongodb.com/api/atlas/v2"
                    )
                ).strip(),
            },
        }
    else:
        atlas_pub = os.getenv("MONGODB_ATLAS_PUBLIC_KEY", "").strip()
        atlas_priv = os.getenv("MONGODB_ATLAS_PRIVATE_KEY", "").strip()
        if atlas_pub and atlas_priv:
            effective["mongodb_atlas"] = {
                "source": "local env",
                "config": {
                    "api_public_key": atlas_pub,
                    "api_private_key": atlas_priv,
                    "project_id": os.getenv("MONGODB_ATLAS_PROJECT_ID", "").strip(),
                    "base_url": os.getenv(
                        "MONGODB_ATLAS_BASE_URL", "https://cloud.mongodb.com/api/atlas/v2"
                    ).strip(),
                },
            }

    mariadb_integration = classified_integrations.get("mariadb")
    if isinstance(mariadb_integration, dict):
        effective["mariadb"] = {
            "source": source_by_service.get("mariadb", "local env"),
            "config": {
                "host": str(mariadb_integration.get("host", "")).strip(),
                "port": mariadb_integration.get("port", 3306),
                "database": str(mariadb_integration.get("database", "")).strip(),
                "username": str(mariadb_integration.get("username", "")).strip(),
                "password": str(mariadb_integration.get("password", "")).strip(),
                "ssl": mariadb_integration.get("ssl", True),
            },
        }
    else:
        mariadb_host = os.getenv("MARIADB_HOST", "").strip()
        mariadb_database = os.getenv("MARIADB_DATABASE", "").strip()
        if mariadb_host and mariadb_database:
            effective["mariadb"] = {
                "source": "local env",
                "config": {
                    "host": mariadb_host,
                    "port": int(os.getenv("MARIADB_PORT", "3306").strip() or "3306") if os.getenv("MARIADB_PORT", "3306").strip().isdigit() else 3306,
                    "database": mariadb_database,
                    "username": os.getenv("MARIADB_USERNAME", "").strip(),
                    "password": os.getenv("MARIADB_PASSWORD", "").strip(),
                    "ssl": os.getenv("MARIADB_SSL", "true").strip().lower() in ("true", "1", "yes"),
                },
            }

    google_docs_integration = classified_integrations.get("google_docs")
    if isinstance(google_docs_integration, dict):
        effective["google_docs"] = {
            "source": source_by_service.get("google_docs", "local env"),
            "config": {
                "credentials_file": str(
                    google_docs_integration.get("credentials_file", "")
                ).strip(),
                "folder_id": str(google_docs_integration.get("folder_id", "")).strip(),
            },
        }
    else:
        # Check env vars
        credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
        if credentials_file and folder_id:
            effective["google_docs"] = {
                "source": "local env",
                "config": {
                    "credentials_file": credentials_file,
                    "folder_id": folder_id,
                },
            }

    vercel_integration = classified_integrations.get("vercel")
    if isinstance(vercel_integration, dict):
        effective["vercel"] = {
            "source": source_by_service.get("vercel", "local env"),
            "config": {
                "api_token": str(vercel_integration.get("api_token", "")).strip(),
                "team_id": str(vercel_integration.get("team_id", "")).strip(),
            },
        }

    opsgenie_integration = classified_integrations.get("opsgenie")
    if isinstance(opsgenie_integration, dict):
        effective["opsgenie"] = {
            "source": source_by_service.get("opsgenie", "local env"),
            "config": {
                "api_key": str(opsgenie_integration.get("api_key", "")).strip(),
                "region": str(opsgenie_integration.get("region", "us")).strip(),
            },
        }

    kafka_integration = classified_integrations.get("kafka")
    if isinstance(kafka_integration, dict):
        effective["kafka"] = {
            "source": source_by_service.get("kafka", "local env"),
            "config": {
                "bootstrap_servers": str(kafka_integration.get("bootstrap_servers", "")).strip(),
                "security_protocol": str(
                    kafka_integration.get("security_protocol", "PLAINTEXT")
                ).strip(),
                "sasl_mechanism": str(kafka_integration.get("sasl_mechanism", "")).strip(),
                "sasl_username": str(kafka_integration.get("sasl_username", "")).strip(),
                "sasl_password": str(kafka_integration.get("sasl_password", "")).strip(),
            },
        }
    else:
        kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
        if kafka_servers:
            effective["kafka"] = {
                "source": "local env",
                "config": {
                    "bootstrap_servers": kafka_servers,
                    "security_protocol": os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip(),
                    "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "").strip(),
                    "sasl_username": os.getenv("KAFKA_SASL_USERNAME", "").strip(),
                    "sasl_password": os.getenv("KAFKA_SASL_PASSWORD", "").strip(),
                },
            }

    clickhouse_integration = classified_integrations.get("clickhouse")
    if isinstance(clickhouse_integration, dict):
        effective["clickhouse"] = {
            "source": source_by_service.get("clickhouse", "local env"),
            "config": {
                "host": str(clickhouse_integration.get("host", "")).strip(),
                "port": clickhouse_integration.get("port", 8123),
                "database": str(clickhouse_integration.get("database", "default")).strip(),
                "username": str(clickhouse_integration.get("username", "default")).strip(),
                "password": str(clickhouse_integration.get("password", "")).strip(),
                "secure": clickhouse_integration.get("secure", False),
            },
        }
    else:
        clickhouse_host = os.getenv("CLICKHOUSE_HOST", "").strip()
        if clickhouse_host:
            effective["clickhouse"] = {
                "source": "local env",
                "config": {
                    "host": clickhouse_host,
                    "port": int(os.getenv("CLICKHOUSE_PORT", "8123") or "8123"),
                    "database": os.getenv("CLICKHOUSE_DATABASE", "default").strip(),
                    "username": os.getenv("CLICKHOUSE_USER", "default").strip(),
                    "password": os.getenv("CLICKHOUSE_PASSWORD", "").strip(),
                    "secure": os.getenv("CLICKHOUSE_SECURE", "false").strip().lower()
                    in ("true", "1", "yes"),
                },
            }

    bitbucket_integration = classified_integrations.get("bitbucket")
    if isinstance(bitbucket_integration, dict):
        effective["bitbucket"] = {
            "source": source_by_service.get("bitbucket", "local env"),
            "config": {
                "workspace": str(bitbucket_integration.get("workspace", "")).strip(),
                "username": str(bitbucket_integration.get("username", "")).strip(),
                "app_password": str(bitbucket_integration.get("app_password", "")).strip(),
            },
        }
    else:
        bitbucket_workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
        if bitbucket_workspace:
            effective["bitbucket"] = {
                "source": "local env",
                "config": {
                    "workspace": bitbucket_workspace,
                    "username": os.getenv("BITBUCKET_USERNAME", "").strip(),
                    "app_password": os.getenv("BITBUCKET_APP_PASSWORD", "").strip(),
                },
            }

    discord_integration = classified_integrations.get("discord")
    if isinstance(discord_integration, dict):
        effective["discord"] = {
            "source": source_by_service.get("discord", "local env"),
            "config": {
                "bot_token": str(discord_integration.get("bot_token", "")).strip(),
                "application_id": str(discord_integration.get("application_id", "")).strip(),
                "public_key": str(discord_integration.get("public_key", "")).strip(),
                "default_channel_id": discord_integration.get("default_channel_id"),
            },
        }
    else:
        discord_bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        if discord_bot_token:
            effective["discord"] = {
                "source": "local env",
                "config": {
                    "bot_token": discord_bot_token,
                    "application_id": os.getenv("DISCORD_APPLICATION_ID", "").strip(),
                    "public_key": os.getenv("DISCORD_PUBLIC_KEY", "").strip(),
                    "default_channel_id": os.getenv("DISCORD_DEFAULT_CHANNEL_ID", "").strip() or None,
                },
            }

    return EffectiveIntegrations.model_validate(effective).model_dump(exclude_none=True)


def _verify_grafana(source: str, config: dict[str, Any]) -> dict[str, str]:
    grafana_config = GrafanaIntegrationConfig.model_validate(config)
    endpoint = grafana_config.endpoint
    api_key = grafana_config.api_key
    if not endpoint or not api_key:
        return _result("grafana", source, "missing", "Missing endpoint or API token.")

    try:
        response = requests.get(
            f"{endpoint}/api/datasources",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return _result("grafana", source, "failed", f"Datasource discovery failed: {exc}")

    datasources = payload if isinstance(payload, list) else []
    supported_types = sorted(
        {
            ds_type
            for ds in datasources
            for ds_type in [str(ds.get("type", "")).lower()]
            if any(keyword in ds_type for keyword in _SUPPORTED_GRAFANA_TYPES)
        }
    )
    if not supported_types:
        return _result(
            "grafana",
            source,
            "failed",
            "Connected, but no Loki, Tempo, or Prometheus datasources were discovered.",
        )

    return _result(
        "grafana",
        source,
        "passed",
        f"Connected to {endpoint} and discovered {', '.join(supported_types)} datasources.",
    )


def _verify_datadog(source: str, config: dict[str, Any]) -> dict[str, str]:
    datadog_integration_config = DatadogIntegrationConfig.model_validate(config)
    datadog_client = DatadogClient(
        DatadogConfig.model_validate(
            datadog_integration_config.model_dump(exclude={"integration_id"})
        )
    )
    if not datadog_client.is_configured:
        return _result("datadog", source, "missing", "Missing API key or application key.")

    result = datadog_client.list_monitors()
    if not result.get("success"):
        return _result(
            "datadog",
            source,
            "failed",
            f"Monitor API check failed: {result.get('error', 'unknown error')}",
        )

    return _result(
        "datadog",
        source,
        "passed",
        f"Connected to api.{datadog_client.config.site} and listed {result.get('total', 0)} monitors.",
    )


def _verify_honeycomb(source: str, config: dict[str, Any]) -> dict[str, str]:
    honeycomb_config = HoneycombIntegrationConfig.model_validate(config)
    honeycomb_client = HoneycombClient(honeycomb_config)
    if not honeycomb_client.is_configured:
        return _result("honeycomb", source, "missing", "Missing Honeycomb API key or dataset.")

    auth_result = honeycomb_client.validate_access()
    if not auth_result.get("success"):
        return _result(
            "honeycomb",
            source,
            "failed",
            f"Auth check failed: {auth_result.get('error', 'unknown error')}",
        )

    query_result = honeycomb_client.run_query(
        {
            "calculations": [{"op": "COUNT"}],
            "time_range": 900,
        },
        limit=1,
    )
    if not query_result.get("success"):
        return _result(
            "honeycomb",
            source,
            "failed",
            f"Query check failed: {query_result.get('error', 'unknown error')}",
        )

    environment = auth_result.get("environment", {})
    environment_slug = (
        str(environment.get("slug", "")).strip() if isinstance(environment, dict) else ""
    )
    environment_label = environment_slug or "classic"
    return _result(
        "honeycomb",
        source,
        "passed",
        (
            f"Connected to {honeycomb_config.base_url} "
            f"(environment {environment_label}) and queried dataset {honeycomb_config.dataset}."
        ),
    )


def _verify_coralogix(source: str, config: dict[str, Any]) -> dict[str, str]:
    coralogix_config = CoralogixIntegrationConfig.model_validate(config)
    coralogix_client = CoralogixClient(coralogix_config)
    if not coralogix_client.is_configured:
        return _result("coralogix", source, "missing", "Missing Coralogix API key or API URL.")

    result = coralogix_client.validate_access()
    if not result.get("success"):
        return _result(
            "coralogix",
            source,
            "failed",
            f"DataPrime check failed: {result.get('error', 'unknown error')}",
        )

    scope: list[str] = []
    if coralogix_config.application_name:
        scope.append(f"application {coralogix_config.application_name}")
    if coralogix_config.subsystem_name:
        scope.append(f"subsystem {coralogix_config.subsystem_name}")
    scope_detail = f" ({', '.join(scope)})" if scope else ""
    return _result(
        "coralogix",
        source,
        "passed",
        (
            f"Connected to {coralogix_config.base_url}{scope_detail}; "
            f"DataPrime returned {result.get('total', 0)} row(s)."
        ),
    )


def _build_sts_client(config: dict[str, Any]) -> tuple[Any, str, str]:
    aws_config = AWSIntegrationConfig.model_validate(config)
    region = aws_config.region
    role_arn = aws_config.role_arn
    external_id = aws_config.external_id
    if role_arn:
        base_sts_client = boto3.client("sts", region_name=region)
        assume_role_args: dict[str, str] = {
            "RoleArn": role_arn,
            "RoleSessionName": "TracerIntegrationVerify",
        }
        if external_id:
            assume_role_args["ExternalId"] = external_id
        credentials = base_sts_client.assume_role(**assume_role_args)["Credentials"]
        return (
            boto3.client(
                "sts",
                region_name=region,
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            ),
            "assume-role",
            region,
        )

    credentials = aws_config.credentials
    return (
        boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=credentials.access_key_id if credentials else "",
            aws_secret_access_key=credentials.secret_access_key if credentials else "",
            aws_session_token=(credentials.session_token if credentials else "") or None,
        ),
        "access-keys",
        region,
    )


def _verify_aws(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        sts_client, auth_mode, region = _build_sts_client(config)
        identity = sts_client.get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        return _result("aws", source, "failed", f"STS caller identity failed: {exc}")

    account_id = str(identity.get("Account", "")).strip()
    arn = str(identity.get("Arn", "")).strip()
    if not account_id or not arn:
        return _result("aws", source, "failed", "STS returned an incomplete caller identity.")

    return _result(
        "aws",
        source,
        "passed",
        f"Authenticated via {auth_mode} in {region} as {arn} (account {account_id}).",
    )


def _verify_slack(
    source: str,
    config: dict[str, Any],
    *,
    send_slack_test: bool,
) -> dict[str, str]:
    try:
        slack_config = SlackWebhookConfig.model_validate(config)
    except Exception:
        return _result("slack", source, "missing", "SLACK_WEBHOOK_URL is not configured.")
    webhook_url = slack_config.webhook_url

    if not send_slack_test:
        return _result(
            "slack",
            source,
            "configured",
            "Incoming webhook configured. Re-run with --send-slack-test to post a test message.",
        )

    try:
        response = httpx.post(
            webhook_url,
            json={"text": "Tracer Flow B connectivity test from local CLI."},
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _result("slack", source, "failed", f"Webhook post failed: {exc}")

    return _result(
        "slack",
        source,
        "passed",
        "Posted a test message through the configured incoming webhook.",
    )


def _verify_tracer(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        tracer_config = TracerIntegrationConfig.model_validate(config)
    except Exception:
        return _result("tracer", source, "missing", "Missing JWT token for Tracer web app access.")
    base_url = tracer_config.base_url or get_tracer_base_url()
    jwt_token = tracer_config.jwt_token

    org_id = extract_org_id_from_jwt(jwt_token)
    if not org_id:
        return _result(
            "tracer", source, "failed", "JWT token does not contain an organization claim."
        )

    try:
        tracer_client = TracerClient(base_url, org_id, jwt_token)
        integrations = tracer_client.get_all_integrations()
    except Exception as exc:  # noqa: BLE001
        return _result("tracer", source, "failed", f"Tracer API check failed: {exc}")

    return _result(
        "tracer",
        source,
        "passed",
        f"Connected to {base_url} for org {org_id} and listed {len(integrations)} integrations.",
    )


def _verify_github(source: str, config: dict[str, Any]) -> dict[str, str]:
    github_config = build_github_mcp_config(config)
    result = validate_github_mcp_config(github_config)
    return _result(
        "github",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_sentry(source: str, config: dict[str, Any]) -> dict[str, str]:
    sentry_config = build_sentry_config(config)
    result = validate_sentry_config(sentry_config)
    return _result(
        "sentry",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_mongodb(source: str, config: dict[str, Any]) -> dict[str, str]:
    mongodb_config = build_mongodb_config(config)
    result = validate_mongodb_config(mongodb_config)
    return _result(
        "mongodb",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_postgresql(source: str, config: dict[str, Any]) -> dict[str, str]:
    postgresql_config = build_postgresql_config(config)
    result = validate_postgresql_config(postgresql_config)
    return _result(
        "postgresql",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_mongodb_atlas(source: str, config: dict[str, Any]) -> dict[str, str]:
    atlas_config = build_mongodb_atlas_config(config)
    result = validate_mongodb_atlas_config(atlas_config)
    return _result(
        "mongodb_atlas",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_mariadb(source: str, config: dict[str, Any]) -> dict[str, str]:
    mariadb_config = build_mariadb_config(config)
    result = validate_mariadb_config(mariadb_config)
    return _result(
        "mariadb",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_google_docs(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Validate Google Docs credentials and folder access."""
    from app.services.google_docs import GoogleDocsClient

    try:
        google_docs_config = GoogleDocsIntegrationConfig.model_validate(config)
    except Exception as err:
        return _result("google_docs", source, "missing", str(err))

    if not google_docs_config.credentials_file or not google_docs_config.folder_id:
        return _result("google_docs", source, "missing", "Missing credentials_file or folder_id.")

    client = GoogleDocsClient(google_docs_config)
    if not client.is_configured:
        return _result(
            "google_docs",
            source,
            "failed",
            f"Credentials file not found: {google_docs_config.credentials_file}",
        )

    result = client.validate_access()
    if not result.get("success"):
        return _result(
            "google_docs",
            source,
            "failed",
            f"Folder access check failed: {result.get('error', 'unknown error')}",
        )

    return _result(
        "google_docs",
        source,
        "passed",
        f"Connected to Drive folder {google_docs_config.folder_id} "
        f"({result.get('file_count', 0)} items in folder).",
    )


def _verify_vercel(
    source: str,
    config: dict[str, Any],
) -> dict[str, str]:
    try:
        vercel_config = VercelConfig.model_validate(config)
    except Exception:
        return _result("vercel", source, "missing", "Missing API token for Vercel access.")
    if not vercel_config.api_token:
        return _result("vercel", source, "missing", "Missing API token for Vercel access.")

    client = VercelClient(vercel_config)
    with client:
        result = client.list_projects()
        if not result.get("success"):
            return _result(
                "vercel",
                source,
                "failed",
                f"Vercel project list failed: {result.get('error', 'unknown error')}",
            )

        base_detail = f"Connected to Vercel API and listed {result.get('total', 0)} project(s)."
        return _result("vercel", source, "passed", base_detail)


def _verify_opsgenie(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        opsgenie_config = OpsGenieConfig.model_validate(
            {
                "api_key": config.get("api_key", ""),
                "region": config.get("region", "us"),
            }
        )
    except Exception as err:
        return _result("opsgenie", source, "missing", str(err))

    client = OpsGenieClient(opsgenie_config)
    if not client.is_configured:
        return _result("opsgenie", source, "missing", "Missing API key.")

    with client:
        result = client.list_alerts(limit=1)
    if not result.get("success"):
        return _result(
            "opsgenie",
            source,
            "failed",
            f"Alert list check failed: {result.get('error', 'unknown error')}",
        )

    return _result(
        "opsgenie",
        source,
        "passed",
        f"Connected to OpsGenie ({opsgenie_config.region.upper()} region); API key accepted.",
    )


def _verify_kafka(source: str, config: dict[str, Any]) -> dict[str, str]:
    from app.integrations.kafka import build_kafka_config, validate_kafka_config

    kafka_config = build_kafka_config(config)
    result = validate_kafka_config(kafka_config)
    return _result(
        "kafka",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_clickhouse(source: str, config: dict[str, Any]) -> dict[str, str]:
    from app.integrations.clickhouse import build_clickhouse_config, validate_clickhouse_config

    clickhouse_config = build_clickhouse_config(config)
    result = validate_clickhouse_config(clickhouse_config)
    return _result(
        "clickhouse",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_bitbucket(source: str, config: dict[str, Any]) -> dict[str, str]:
    from app.integrations.bitbucket import build_bitbucket_config, validate_bitbucket_config

    bitbucket_config = build_bitbucket_config(config)
    result = validate_bitbucket_config(bitbucket_config)
    return _result(
        "bitbucket",
        source,
        "passed" if result.ok else "failed",
        result.detail,
    )


def _verify_discord(source: str, config: dict[str, Any]) -> dict[str, str]:
    bot_token = str(config.get("bot_token", "")).strip()
    if not bot_token:
        return _result("discord", source, "missing", "Missing bot token.")

    try:
        response = httpx.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        return _result("discord", source, "failed", f"Bot token validation failed: {exc}")

    if not response.is_success:
        return _result(
            "discord",
            source,
            "failed",
            f"Discord API returned {response.status_code}: {response.text[:200]}",
        )

    data = response.json()

    username = str(data.get("username", "")).strip()
    bot_id = str(data.get("id", "")).strip()
    return _result(
        "discord",
        source,
        "passed",
        f"Connected to Discord API as bot {username} (id {bot_id}).",
    )


def verify_integrations(
    service: str | None = None,
    *,
    send_slack_test: bool = False,
) -> list[dict[str, str]]:
    """Run verification checks for configured integrations."""
    effective_integrations = resolve_effective_integrations()

    services = [service] if service else list(SUPPORTED_VERIFY_SERVICES)
    results: list[dict[str, str]] = []
    for current_service in services:
        if current_service == "slack":
            integration = effective_integrations.get("slack")
            if not integration:
                results.append(
                    _result("slack", "-", "missing", "SLACK_WEBHOOK_URL is not configured.")
                )
                continue
            results.append(
                _verify_slack(
                    source=str(integration["source"]),
                    config=dict(integration["config"]),
                    send_slack_test=send_slack_test,
                )
            )
            continue

        integration = effective_integrations.get(current_service)
        if not integration:
            results.append(
                _result(current_service, "-", "missing", "Not configured in local store or env.")
            )
            continue

        source = str(integration["source"])
        config = dict(integration["config"])
        if current_service == "grafana":
            results.append(_verify_grafana(source, config))
        elif current_service == "datadog":
            results.append(_verify_datadog(source, config))
        elif current_service == "honeycomb":
            results.append(_verify_honeycomb(source, config))
        elif current_service == "coralogix":
            results.append(_verify_coralogix(source, config))
        elif current_service == "aws":
            results.append(_verify_aws(source, config))
        elif current_service == "tracer":
            results.append(_verify_tracer(source, config))
        elif current_service == "github":
            results.append(_verify_github(source, config))
        elif current_service == "sentry":
            results.append(_verify_sentry(source, config))
        elif current_service == "mongodb":
            results.append(_verify_mongodb(source, config))
        elif current_service == "postgresql":
            results.append(_verify_postgresql(source, config))
        elif current_service == "mongodb_atlas":
            results.append(_verify_mongodb_atlas(source, config))
        elif current_service == "mariadb":
            results.append(_verify_mariadb(source, config))
        elif current_service == "google_docs":
            results.append(_verify_google_docs(source, config))
        elif current_service == "vercel":
            results.append(_verify_vercel(source, config))
        elif current_service == "opsgenie":
            results.append(_verify_opsgenie(source, config))
        elif current_service == "kafka":
            results.append(_verify_kafka(source, config))
        elif current_service == "clickhouse":
            results.append(_verify_clickhouse(source, config))
        elif current_service == "bitbucket":
            results.append(_verify_bitbucket(source, config))
        elif current_service == "discord":
            results.append(_verify_discord(source, config))

    return results


def format_verification_results(results: list[dict[str, str]]) -> str:
    """Render verification results as a compact terminal table."""
    lines = ["", "  SERVICE    SOURCE       STATUS      DETAIL"]
    for result in results:
        lines.append(
            f"  {result['service']:<10}"
            f"{result['source']:<13}"
            f"{result['status']:<12}"
            f"{result['detail']}"
        )
    lines.append("")
    return "\n".join(lines)


def verification_exit_code(
    results: list[dict[str, str]],
    *,
    requested_service: str | None = None,
) -> int:
    """Return a CLI exit code for a verification run."""
    if any(result["status"] == "failed" for result in results):
        return 1

    if requested_service:
        return 1 if any(result["status"] in {"missing", "failed"} for result in results) else 0

    core_results = [result for result in results if result["service"] in CORE_VERIFY_SERVICES]
    if not any(result["status"] == "passed" for result in core_results):
        return 1
    return 0
