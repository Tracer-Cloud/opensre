"""Verification helpers for local and env-backed integration setup."""

from __future__ import annotations

from typing import Any

import boto3
import httpx
import requests

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.config import get_tracer_base_url
from app.integrations.azure_sql import build_azure_sql_config, validate_azure_sql_config
from app.integrations.catalog import (
    resolve_effective_integrations as _resolve_effective_integrations,
)
from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.mariadb import build_mariadb_config, validate_mariadb_config
from app.integrations.models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    DatadogIntegrationConfig,
    GoogleDocsIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
    TracerIntegrationConfig,
)
from app.integrations.mongodb import build_mongodb_config, validate_mongodb_config
from app.integrations.mongodb_atlas import build_mongodb_atlas_config, validate_mongodb_atlas_config
from app.integrations.mysql import build_mysql_config, validate_mysql_config
from app.integrations.openclaw import build_openclaw_config, validate_openclaw_config
from app.integrations.postgresql import build_postgresql_config, validate_postgresql_config
from app.integrations.sentry import build_sentry_config, validate_sentry_config
from app.services.alertmanager import AlertmanagerClient, AlertmanagerConfig
from app.services.coralogix import CoralogixClient
from app.services.datadog.client import DatadogClient, DatadogConfig
from app.services.honeycomb import HoneycombClient
from app.services.opsgenie import OpsGenieClient, OpsGenieConfig
from app.services.tracer_client.client import TracerClient
from app.services.vercel.client import VercelClient, VercelConfig

SUPPORTED_VERIFY_SERVICES = (
    "alertmanager",
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
    "azure_sql",
    "mongodb_atlas",
    "mariadb",
    "google_docs",
    "vercel",
    "opsgenie",
    "kafka",
    "clickhouse",
    "bitbucket",
    "discord",
    "mysql",
    "openclaw",
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
    """Resolve effective local integrations from ~/.tracer and environment variables."""
    return _resolve_effective_integrations()


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


def _verify_azure_sql(source: str, config: dict[str, Any]) -> dict[str, str]:
    azure_sql_config = build_azure_sql_config(config)
    result = validate_azure_sql_config(azure_sql_config)
    return _result(
        "azure_sql",
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


def _verify_alertmanager(source: str, config: dict[str, Any]) -> dict[str, str]:
    base_url = str(config.get("base_url", ""))
    if not base_url:
        return _result("alertmanager", source, "missing", "Missing base_url.")

    try:
        alertmanager_config = AlertmanagerConfig.model_validate(
            {
                "base_url": base_url,
                "bearer_token": config.get("bearer_token", ""),
                "username": config.get("username", ""),
                "password": config.get("password", ""),
            }
        )
    except Exception as err:
        return _result("alertmanager", source, "missing", str(err))

    with AlertmanagerClient(alertmanager_config) as client:
        result = client.get_status()

    if not result.get("success"):
        return _result(
            "alertmanager",
            source,
            "failed",
            f"Status check failed: {result.get('error', 'unknown error')}",
        )

    status_data = result.get("status", {})
    cluster_status = status_data.get("cluster", {}).get("status", "unknown") if isinstance(status_data, dict) else "ok"
    return _result(
        "alertmanager",
        source,
        "passed",
        f"Connected to Alertmanager at {base_url}; cluster status: {cluster_status}.",
    )


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


def _verify_openclaw(source: str, config: dict[str, Any]) -> dict[str, str]:
    try:
        openclaw_config = build_openclaw_config(config)
    except Exception as exc:
        return _result("openclaw", source, "failed", f"Invalid OpenClaw config: {exc}")

    if not openclaw_config.is_configured:
        return _result(
            "openclaw",
            source,
            "missing",
            "OpenClaw is not configured: provide a URL (HTTP/SSE) or command (stdio).",
        )

    result = validate_openclaw_config(openclaw_config)
    status = "passed" if result.ok else "failed"
    return _result("openclaw", source, status, result.detail)


def _verify_mysql(source: str, config: dict[str, Any]) -> dict[str, str]:
    mysql_config = build_mysql_config(config)
    result = validate_mysql_config(mysql_config)
    return _result(
        "mysql",
        source,
        "passed" if result.ok else "failed",
        result.detail,
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
        elif current_service == "azure_sql":
            results.append(_verify_azure_sql(source, config))
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
        elif current_service == "openclaw":
            results.append(_verify_openclaw(source, config))
        elif current_service == "mysql":
            results.append(_verify_mysql(source, config))
        elif current_service == "alertmanager":
            results.append(_verify_alertmanager(source, config))

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
