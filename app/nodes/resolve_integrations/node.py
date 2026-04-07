"""Resolve integrations node - fetches org integrations and classifies by service.

Runs early in the investigation pipeline (after extract_alert) to make
integration credentials available for all downstream nodes. This replaces
per-node credential fetching with a single upfront resolution.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.integrations.github_mcp import build_github_mcp_config
from app.integrations.gitlab import DEFAULT_GITLAB_BASE_URL, build_gitlab_config
from app.integrations.models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    OpsGenieIntegrationConfig,
)
from app.integrations.mongodb import build_mongodb_config
from app.integrations.sentry import build_sentry_config
from app.output import get_tracker
from app.services.vercel import VercelConfig
from app.state import InvestigationState

logger = logging.getLogger(__name__)

# Services we skip (already handled by the webhook layer or not queryable)
_SKIP_SERVICES = {"slack"}

# Mapping from integration service names to canonical keys (case-insensitive lookup below)
# EKS uses the same AWS role — no separate EKS integration key
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
    "vercel": "vercel",
    "opsgenie": "opsgenie",
    "bitbucket": "bitbucket",
    "snowflake": "snowflake",
    "azure": "azure",
    "azure monitor": "azure",
    "azure_monitor": "azure",
    "openobserve": "openobserve",
    "open observe": "openobserve",
    "opensearch": "opensearch",
    "open search": "opensearch",
}


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _classify_integrations(
    integrations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify active integrations by service into a structured dict.

    Returns:
        {
            "grafana": {"endpoint": "...", "api_key": "...", "integration_id": "..."},
            "aws": {"role_arn": "...", "external_id": "...", "integration_id": "..."},
            ...
            "_all": [<raw integration records>]
        }
    """
    resolved: dict[str, Any] = {}

    active = [i for i in integrations if i.get("status") == "active"]

    for integration in active:
        service = str(integration.get("service") or "").strip()
        if not service:
            continue

        service_lower = service.lower()

        if service_lower in _SKIP_SERVICES:
            continue

        key = _SERVICE_KEY_MAP.get(service_lower, service_lower)
        credentials = integration.get("credentials", {})

        if key in ("grafana", "grafana_local"):
            try:
                grafana_config = GrafanaIntegrationConfig.model_validate({
                    "endpoint": credentials.get("endpoint", ""),
                    "api_key": credentials.get("api_key", ""),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if not grafana_config.endpoint:
                continue
            if grafana_config.is_local:
                # Always treat localhost Grafana as grafana_local (Loki only, anonymous auth)
                resolved["grafana_local"] = {
                    "endpoint": grafana_config.endpoint,
                    "api_key": "",
                    "integration_id": grafana_config.integration_id,
                }
            elif grafana_config.api_key and grafana_config.api_key != "local":
                resolved["grafana"] = grafana_config.model_dump()

        elif key == "aws":
            if "aws" in resolved:
                continue
            raw_config: dict[str, Any] = {
                "region": credentials.get("region", "us-east-1"),
                "role_arn": integration.get("role_arn", ""),
                "external_id": integration.get("external_id", ""),
                "integration_id": integration.get("id", ""),
            }
            if credentials.get("access_key_id") and credentials.get("secret_access_key"):
                raw_config["credentials"] = {
                    "access_key_id": credentials.get("access_key_id", ""),
                    "secret_access_key": credentials.get("secret_access_key", ""),
                    "session_token": credentials.get("session_token", ""),
                }
            try:
                resolved["aws"] = AWSIntegrationConfig.model_validate(raw_config).model_dump(
                    exclude_none=True
                )
            except Exception:
                continue

        elif key == "datadog":
            try:
                datadog_config = DatadogIntegrationConfig.model_validate({
                    "api_key": credentials.get("api_key", ""),
                    "app_key": credentials.get("app_key", ""),
                    "site": credentials.get("site", "datadoghq.com"),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if datadog_config.api_key and datadog_config.app_key:
                resolved["datadog"] = datadog_config.model_dump()

        elif key == "honeycomb":
            try:
                honeycomb_config = HoneycombIntegrationConfig.model_validate({
                    "api_key": credentials.get("api_key", ""),
                    "dataset": credentials.get("dataset", ""),
                    "base_url": credentials.get("base_url", ""),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if honeycomb_config.api_key:
                resolved["honeycomb"] = honeycomb_config.model_dump()

        elif key == "coralogix":
            try:
                coralogix_config = CoralogixIntegrationConfig.model_validate({
                    "api_key": credentials.get("api_key", ""),
                    "base_url": credentials.get("base_url", ""),
                    "application_name": credentials.get("application_name", ""),
                    "subsystem_name": credentials.get("subsystem_name", ""),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if coralogix_config.api_key:
                resolved["coralogix"] = coralogix_config.model_dump()

        elif key == "github":
            try:
                github_config = build_github_mcp_config({
                    "url": credentials.get("url", ""),
                    "mode": credentials.get("mode", "streamable-http"),
                    "command": credentials.get("command", ""),
                    "args": credentials.get("args", []),
                    "auth_token": credentials.get("auth_token", ""),
                    "toolsets": credentials.get("toolsets", []),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            resolved["github"] = github_config.model_dump()

        elif key == "sentry":
            try:
                sentry_config = build_sentry_config({
                    "base_url": credentials.get("base_url", "https://sentry.io"),
                    "organization_slug": credentials.get("organization_slug", ""),
                    "auth_token": credentials.get("auth_token", ""),
                    "project_slug": credentials.get("project_slug", ""),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if sentry_config.organization_slug and sentry_config.auth_token:
                resolved["sentry"] = sentry_config.model_dump()

        elif key == "gitlab":
            try:
                gitlab_config = build_gitlab_config({
                    "base_url": credentials.get("base_url", ""),
                    "auth_token": credentials.get("auth_token", ""),
                })
            except Exception:
                continue
            resolved["gitlab"] = gitlab_config.model_dump()
        elif key == "mongodb":
            try:
                mongodb_config = build_mongodb_config({
                    "connection_string": credentials.get("connection_string", ""),
                    "database": credentials.get("database", ""),
                    "auth_source": credentials.get("auth_source", "admin"),
                    "tls": credentials.get("tls", True),
                })
            except Exception:
                continue

            if mongodb_config.connection_string:
                resolved["mongodb"] = mongodb_config.model_dump()

        elif key == "vercel":
            try:
                vercel_config = VercelConfig.model_validate({
                    "api_token": credentials.get("api_token", ""),
                    "team_id": credentials.get("team_id", ""),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue

            if vercel_config.api_token:
                resolved["vercel"] = vercel_config.model_dump()

        elif key == "opsgenie":
            try:
                opsgenie_config = OpsGenieIntegrationConfig.model_validate({
                    "api_key": credentials.get("api_key", ""),
                    "region": credentials.get("region", "us"),
                    "integration_id": integration.get("id", ""),
                })
            except Exception:
                continue
            if opsgenie_config.api_key:
                resolved["opsgenie"] = opsgenie_config.model_dump()

        elif key == "bitbucket":
            workspace = str(credentials.get("workspace", "")).strip()
            username = str(credentials.get("username", "")).strip()
            app_password = str(credentials.get("app_password", "")).strip()
            if workspace and username and app_password:
                resolved["bitbucket"] = {
                    "workspace": workspace,
                    "username": username,
                    "app_password": app_password,
                    "base_url": str(
                        credentials.get("base_url", "https://api.bitbucket.org/2.0")
                    ).strip() or "https://api.bitbucket.org/2.0",
                    "max_results": max(
                        1,
                        min(
                            _coerce_int(credentials.get("max_results", 25), default=25),
                            100,
                        ),
                    ),
                    "integration_id": str(integration.get("id", "")).strip(),
                }

        elif key == "snowflake":
            account_identifier = str(
                credentials.get("account_identifier", credentials.get("account", ""))
            ).strip()
            user = str(credentials.get("user", "")).strip()
            password = str(credentials.get("password", "")).strip()
            token = str(credentials.get("token", "")).strip()
            if account_identifier and (token or (user and password)):
                resolved["snowflake"] = {
                    "account_identifier": account_identifier,
                    "user": user,
                    "password": password,
                    "token": token,
                    "warehouse": str(credentials.get("warehouse", "")).strip(),
                    "role": str(credentials.get("role", "")).strip(),
                    "database": str(credentials.get("database", "")).strip(),
                    "schema": str(credentials.get("schema", "")).strip(),
                    "max_results": max(
                        1,
                        min(_coerce_int(credentials.get("max_results", 50), default=50), 200),
                    ),
                    "integration_id": str(integration.get("id", "")).strip(),
                }

        elif key == "azure":
            workspace_id = str(
                credentials.get("workspace_id", credentials.get("log_analytics_workspace_id", ""))
            ).strip()
            access_token = str(
                credentials.get("access_token", credentials.get("token", ""))
            ).strip()
            if workspace_id and access_token:
                resolved["azure"] = {
                    "workspace_id": workspace_id,
                    "access_token": access_token,
                    "endpoint": str(
                        credentials.get("endpoint", "https://api.loganalytics.io")
                    ).strip() or "https://api.loganalytics.io",
                    "tenant_id": str(credentials.get("tenant_id", "")).strip(),
                    "subscription_id": str(credentials.get("subscription_id", "")).strip(),
                    "max_results": max(
                        1,
                        min(_coerce_int(credentials.get("max_results", 100), default=100), 200),
                    ),
                    "integration_id": str(integration.get("id", "")).strip(),
                }

        elif key == "openobserve":
            base_url = str(
                credentials.get("base_url", credentials.get("url", ""))
            ).strip()
            api_token = str(credentials.get("api_token", credentials.get("token", ""))).strip()
            username = str(credentials.get("username", "")).strip()
            password = str(credentials.get("password", "")).strip()
            if base_url and (api_token or (username and password)):
                resolved["openobserve"] = {
                    "base_url": base_url.rstrip("/"),
                    "org": str(
                        credentials.get("org", credentials.get("organization", "default"))
                    ).strip()
                    or "default",
                    "api_token": api_token,
                    "username": username,
                    "password": password,
                    "stream": str(credentials.get("stream", "")).strip(),
                    "max_results": max(
                        1,
                        min(_coerce_int(credentials.get("max_results", 100), default=100), 200),
                    ),
                    "integration_id": str(integration.get("id", "")).strip(),
                }

        elif key == "opensearch":
            url = str(credentials.get("url", credentials.get("base_url", ""))).strip()
            if url:
                resolved["opensearch"] = {
                    "url": url.rstrip("/"),
                    "api_key": str(credentials.get("api_key", "")).strip(),
                    "index_pattern": str(
                        credentials.get("index_pattern", credentials.get("index", "*"))
                    ).strip()
                    or "*",
                    "max_results": max(
                        1,
                        min(_coerce_int(credentials.get("max_results", 100), default=100), 200),
                    ),
                    "integration_id": str(integration.get("id", "")).strip(),
                }

        else:
            resolved[key] = {
                "credentials": credentials,
                "integration_id": integration.get("id", ""),
            }

    resolved["_all"] = active
    return resolved


def _decode_org_id_from_token(token: str) -> str:
    import base64
    import json as _json

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("organization") or claims.get("org_id") or ""
    except Exception:
        logger.debug("Failed to decode org_id from JWT token", exc_info=True)
        return ""


def _strip_bearer(token: str) -> str:
    if token.lower().startswith("bearer "):
        return token.split(None, 1)[1].strip()
    return token


def _load_env_integrations() -> list[dict[str, Any]]:
    """Build integration records from local environment variables."""
    integrations: list[dict[str, Any]] = []

    grafana_endpoint = os.getenv("GRAFANA_INSTANCE_URL", "").strip()
    grafana_api_key = os.getenv("GRAFANA_READ_TOKEN", "").strip()
    if grafana_endpoint and grafana_api_key:
        grafana_config = GrafanaIntegrationConfig.model_validate({
            "endpoint": grafana_endpoint,
            "api_key": grafana_api_key,
        })
        integrations.append({
            "id": "env-grafana",
            "service": "grafana",
            "status": "active",
            "credentials": {
                "endpoint": grafana_config.endpoint,
                "api_key": grafana_config.api_key,
            },
        })

    datadog_api_key = os.getenv("DD_API_KEY", "").strip()
    datadog_app_key = os.getenv("DD_APP_KEY", "").strip()
    datadog_site = os.getenv("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
    if datadog_api_key and datadog_app_key:
        datadog_config = DatadogIntegrationConfig.model_validate({
            "api_key": datadog_api_key,
            "app_key": datadog_app_key,
            "site": datadog_site,
        })
        integrations.append({
            "id": "env-datadog",
            "service": "datadog",
            "status": "active",
            "credentials": datadog_config.model_dump(exclude={"integration_id"}),
        })

    honeycomb_api_key = os.getenv("HONEYCOMB_API_KEY", "").strip()
    if honeycomb_api_key:
        honeycomb_config = HoneycombIntegrationConfig.model_validate({
            "api_key": honeycomb_api_key,
            "dataset": os.getenv("HONEYCOMB_DATASET", "").strip(),
            "base_url": os.getenv("HONEYCOMB_API_URL", "").strip(),
        })
        integrations.append({
            "id": "env-honeycomb",
            "service": "honeycomb",
            "status": "active",
            "credentials": honeycomb_config.model_dump(exclude={"integration_id"}),
        })

    coralogix_api_key = os.getenv("CORALOGIX_API_KEY", "").strip()
    if coralogix_api_key:
        coralogix_config = CoralogixIntegrationConfig.model_validate({
            "api_key": coralogix_api_key,
            "base_url": os.getenv("CORALOGIX_API_URL", "").strip(),
            "application_name": os.getenv("CORALOGIX_APPLICATION_NAME", "").strip(),
            "subsystem_name": os.getenv("CORALOGIX_SUBSYSTEM_NAME", "").strip(),
        })
        integrations.append({
            "id": "env-coralogix",
            "service": "coralogix",
            "status": "active",
            "credentials": coralogix_config.model_dump(exclude={"integration_id"}),
        })

    aws_role_arn = os.getenv("AWS_ROLE_ARN", "").strip()
    aws_external_id = os.getenv("AWS_EXTERNAL_ID", "").strip()
    aws_region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    aws_session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()
    if aws_role_arn:
        aws_config = AWSIntegrationConfig.model_validate({
            "role_arn": aws_role_arn,
            "external_id": aws_external_id,
            "region": aws_region,
        })
        integrations.append({
            "id": "env-aws",
            "service": "aws",
            "status": "active",
            "role_arn": aws_config.role_arn,
            "external_id": aws_config.external_id,
            "credentials": {"region": aws_config.region},
        })
    elif aws_access_key_id and aws_secret_access_key:
        aws_config = AWSIntegrationConfig.model_validate({
            "region": aws_region,
            "credentials": {
                "access_key_id": aws_access_key_id,
                "secret_access_key": aws_secret_access_key,
                "session_token": aws_session_token,
            },
        })
        aws_credentials = aws_config.credentials
        assert aws_credentials is not None
        integrations.append({
            "id": "env-aws",
            "service": "aws",
            "status": "active",
            "credentials": {
                "access_key_id": aws_credentials.access_key_id,
                "secret_access_key": aws_credentials.secret_access_key,
                "session_token": aws_credentials.session_token,
                "region": aws_config.region,
            },
        })

    github_mode = os.getenv("GITHUB_MCP_MODE", "streamable-http").strip() or "streamable-http"
    github_url = os.getenv("GITHUB_MCP_URL", "").strip()
    github_command = os.getenv("GITHUB_MCP_COMMAND", "").strip()
    github_args = os.getenv("GITHUB_MCP_ARGS", "").strip()
    github_auth_token = os.getenv("GITHUB_MCP_AUTH_TOKEN", "").strip()
    github_toolsets = os.getenv("GITHUB_MCP_TOOLSETS", "").strip()
    if (github_mode == "stdio" and github_command) or (github_mode != "stdio" and github_url):
        github_config = build_github_mcp_config({
            "url": github_url,
            "mode": github_mode,
            "command": github_command,
            "args": [part for part in github_args.split() if part],
            "auth_token": github_auth_token,
            "toolsets": [part.strip() for part in github_toolsets.split(",") if part.strip()],
        })
        integrations.append({
            "id": "env-github",
            "service": "github",
            "status": "active",
            "credentials": github_config.model_dump(exclude={"integration_id"}),
        })

    sentry_org_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    sentry_auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if sentry_org_slug and sentry_auth_token:
        sentry_config = build_sentry_config({
            "base_url": os.getenv("SENTRY_URL", "https://sentry.io").strip() or "https://sentry.io",
            "organization_slug": sentry_org_slug,
            "auth_token": sentry_auth_token,
            "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
        })
        integrations.append({
            "id": "env-sentry",
            "service": "sentry",
            "status": "active",
            "credentials": sentry_config.model_dump(exclude={"integration_id"}),
        })

    gitlab_access_token = os.getenv("GITLAB_ACCESS_TOKEN", "").strip()
    if gitlab_access_token:
        gitlab_config = build_gitlab_config({
            "base_url": os.getenv("GITLAB_BASE_URL", DEFAULT_GITLAB_BASE_URL).strip() or DEFAULT_GITLAB_BASE_URL,
            "auth_token": gitlab_access_token,
        })
        integrations.append({
            "id": "env-gitlab",
            "service": "gitlab",
            "status": "active",
            "credentials": gitlab_config.model_dump(),
        })
    mongodb_connection_string = os.getenv("MONGODB_CONNECTION_STRING", "").strip()
    if mongodb_connection_string:
        mongodb_config = build_mongodb_config({
            "connection_string": mongodb_connection_string,
            "database": os.getenv("MONGODB_DATABASE", "").strip(),
            "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin").strip() or "admin",
            "tls": os.getenv("MONGODB_TLS", "true").strip().lower() in ("true", "1", "yes"),
        })
        integrations.append({
            "id": "env-mongodb",
            "service": "mongodb",
            "status": "active",
            "credentials": mongodb_config.model_dump(exclude={"integration_id"}),
        })

    vercel_api_token = os.getenv("VERCEL_API_TOKEN", "").strip()
    if vercel_api_token:
        vercel_config = VercelConfig.model_validate({
            "api_token": vercel_api_token,
            "team_id": os.getenv("VERCEL_TEAM_ID", "").strip(),
        })
        integrations.append({
            "id": "env-vercel",
            "service": "vercel",
            "status": "active",
            "credentials": vercel_config.model_dump(exclude={"integration_id"}),
        })

    opsgenie_api_key = os.getenv("OPSGENIE_API_KEY", "").strip()
    if opsgenie_api_key:
        opsgenie_config = OpsGenieIntegrationConfig.model_validate({
            "api_key": opsgenie_api_key,
            "region": os.getenv("OPSGENIE_REGION", "us").strip() or "us",
        })
        integrations.append({
            "id": "env-opsgenie",
            "service": "opsgenie",
            "status": "active",
            "credentials": opsgenie_config.model_dump(exclude={"integration_id"}),
        })

    bitbucket_workspace = os.getenv("BITBUCKET_WORKSPACE", "").strip()
    bitbucket_username = os.getenv("BITBUCKET_USERNAME", "").strip()
    bitbucket_app_password = os.getenv("BITBUCKET_APP_PASSWORD", "").strip()
    if bitbucket_workspace and bitbucket_username and bitbucket_app_password:
        integrations.append({
            "id": "env-bitbucket",
            "service": "bitbucket",
            "status": "active",
            "credentials": {
                "workspace": bitbucket_workspace,
                "username": bitbucket_username,
                "app_password": bitbucket_app_password,
                "base_url": os.getenv(
                    "BITBUCKET_BASE_URL", "https://api.bitbucket.org/2.0"
                ).strip()
                or "https://api.bitbucket.org/2.0",
                "max_results": _coerce_int(os.getenv("BITBUCKET_MAX_RESULTS", "25"), default=25),
            },
        })

    snowflake_account = str(
        os.getenv("SNOWFLAKE_ACCOUNT_IDENTIFIER", "") or os.getenv("SNOWFLAKE_ACCOUNT", "")
    ).strip()
    snowflake_token = os.getenv("SNOWFLAKE_TOKEN", "").strip()
    snowflake_user = os.getenv("SNOWFLAKE_USER", "").strip()
    snowflake_password = os.getenv("SNOWFLAKE_PASSWORD", "").strip()
    if snowflake_account and (snowflake_token or (snowflake_user and snowflake_password)):
        integrations.append({
            "id": "env-snowflake",
            "service": "snowflake",
            "status": "active",
            "credentials": {
                "account_identifier": snowflake_account,
                "token": snowflake_token,
                "user": snowflake_user,
                "password": snowflake_password,
                "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "").strip(),
                "role": os.getenv("SNOWFLAKE_ROLE", "").strip(),
                "database": os.getenv("SNOWFLAKE_DATABASE", "").strip(),
                "schema": os.getenv("SNOWFLAKE_SCHEMA", "").strip(),
                "max_results": _coerce_int(os.getenv("SNOWFLAKE_MAX_RESULTS", "50"), default=50),
            },
        })

    azure_workspace_id = os.getenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    azure_access_token = os.getenv("AZURE_LOG_ANALYTICS_TOKEN", "").strip()
    if azure_workspace_id and azure_access_token:
        integrations.append({
            "id": "env-azure",
            "service": "azure",
            "status": "active",
            "credentials": {
                "workspace_id": azure_workspace_id,
                "access_token": azure_access_token,
                "endpoint": os.getenv("AZURE_LOG_ANALYTICS_ENDPOINT", "https://api.loganalytics.io").strip()
                or "https://api.loganalytics.io",
                "tenant_id": os.getenv("AZURE_TENANT_ID", "").strip(),
                "subscription_id": os.getenv("AZURE_SUBSCRIPTION_ID", "").strip(),
                "max_results": _coerce_int(os.getenv("AZURE_MAX_RESULTS", "100"), default=100),
            },
        })

    openobserve_url = os.getenv("OPENOBSERVE_URL", "").strip()
    openobserve_token = os.getenv("OPENOBSERVE_TOKEN", "").strip()
    openobserve_username = os.getenv("OPENOBSERVE_USERNAME", "").strip()
    openobserve_password = os.getenv("OPENOBSERVE_PASSWORD", "").strip()
    if openobserve_url and (openobserve_token or (openobserve_username and openobserve_password)):
        integrations.append({
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
                "max_results": _coerce_int(os.getenv("OPENOBSERVE_MAX_RESULTS", "100"), default=100),
            },
        })

    opensearch_url = os.getenv("OPENSEARCH_URL", "").strip()
    if opensearch_url:
        integrations.append({
            "id": "env-opensearch",
            "service": "opensearch",
            "status": "active",
            "credentials": {
                "url": opensearch_url.rstrip("/"),
                "api_key": os.getenv("OPENSEARCH_API_KEY", "").strip(),
                "index_pattern": os.getenv("OPENSEARCH_INDEX_PATTERN", "*").strip() or "*",
                "max_results": _coerce_int(os.getenv("OPENSEARCH_MAX_RESULTS", "100"), default=100),
            },
        })

    return integrations


def _merge_local_integrations(
    store_integrations: list[dict[str, Any]],
    env_integrations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge local store and env integrations, preferring store entries by service."""
    return _merge_integrations_by_service(env_integrations, store_integrations)


def _merge_integrations_by_service(
    *integration_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge integration records by service, letting later groups override earlier ones."""
    merged_by_service: dict[str, dict[str, Any]] = {}
    for integration_group in integration_groups:
        for integration in integration_group:
            service = integration.get("service", "")
            if service:
                merged_by_service[service] = integration
    return list(merged_by_service.values())


@traceable(name="node_resolve_integrations")
def node_resolve_integrations(state: InvestigationState, config: RunnableConfig | None = None) -> dict:
    """Fetch all org integrations and classify them by service.

    Priority:
      1. _auth_token from state (Slack webhook / inbound request) — remote API only, no local fallback
      2. JWT_TOKEN env var — remote API, with local store/env filling missing services
      3. Local sources: ~/.tracer/integrations.json, plus env-based integrations for standalone use
    """
    if state.get("resolved_integrations"):
        return {}

    tracker = get_tracker()
    tracker.start("resolve_integrations", "Fetching org integrations")
    org_id = state.get("org_id", "")

    configurable = (config or {}).get("configurable", {})
    auth_user = configurable.get("langgraph_auth_user", {})
    webhook_token = _strip_bearer(
        (auth_user.get("token", "") or state.get("_auth_token", "")).strip()
    )
    if webhook_token:
        if not org_id:
            org_id = _decode_org_id_from_token(webhook_token)
        if not org_id:
            logger.warning("_auth_token present but could not decode org_id")
            tracker.complete(
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
                message="Auth token present but org_id could not be determined",
            )
            return {"resolved_integrations": {}}
        try:
            from app.services.tracer_client import get_tracer_client_for_org
            all_integrations = get_tracer_client_for_org(org_id, webhook_token).get_all_integrations()
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            tracker.complete(
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
                message="Remote integrations fetch failed",
            )
            return {"resolved_integrations": {}}

    else:
        # Priority 2: JWT_TOKEN env var
        env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
        if env_token:
            if not org_id:
                org_id = _decode_org_id_from_token(env_token)
            if not org_id:
                return _resolve_from_local_sources(tracker)
            try:
                from app.services.tracer_client import get_tracer_client_for_org
                all_integrations = get_tracer_client_for_org(org_id, env_token).get_all_integrations()
            except Exception:
                logger.debug("Remote integrations fetch failed for org %s, falling back to local", org_id, exc_info=True)
                return _resolve_from_local_sources(tracker)
            return _resolve_remote_with_local_fallback(all_integrations, tracker)
        else:
            # Priority 3: local sources only
            return _resolve_from_local_sources(tracker)

    resolved = _classify_integrations(all_integrations)
    services = [k for k in resolved if k != "_all"]

    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved integrations: {services}" if services else "No active integrations found",
    )

    return {"resolved_integrations": resolved}


def _resolve_from_local_sources(tracker: Any) -> dict:
    from app.integrations.store import STORE_PATH, load_integrations

    store_integrations = load_integrations()
    # Env vars are only used as a fallback when the store has no integrations at all.
    env_integrations = _load_env_integrations() if not store_integrations else []
    integrations = _merge_local_integrations(store_integrations, env_integrations)
    if not integrations:
        tracker.complete(
            "resolve_integrations",
            fields_updated=["resolved_integrations"],
            message=(
                "No auth context and no local integrations found "
                f"(store: {STORE_PATH}, env fallback checked)"
            ),
        )
        return {"resolved_integrations": {}}

    resolved = _classify_integrations(integrations)
    services = [k for k in resolved if k != "_all"]
    source_labels: list[str] = []
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")
    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=(
            f"Resolved local integrations from {', '.join(source_labels)}: {services}"
            if source_labels
            else f"Resolved local integrations: {services}"
        ),
    )
    return {"resolved_integrations": resolved}


def _resolve_remote_with_local_fallback(
    remote_integrations: list[dict[str, Any]],
    tracker: Any,
) -> dict:
    from app.integrations.store import load_integrations

    store_integrations = load_integrations()
    env_integrations = _load_env_integrations()
    integrations = _merge_integrations_by_service(
        env_integrations,
        store_integrations,
        remote_integrations,
    )
    resolved = _classify_integrations(integrations)
    services = [k for k in resolved if k != "_all"]

    source_labels = ["remote"]
    if store_integrations:
        source_labels.append("store")
    if env_integrations:
        source_labels.append("env")

    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=(
            f"Resolved integrations from {', '.join(source_labels)}: {services}"
            if services
            else "No active integrations found"
        ),
    )
    return {"resolved_integrations": resolved}
