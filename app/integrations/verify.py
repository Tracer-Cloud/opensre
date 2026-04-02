"""Verification helpers for local and env-backed integration setup."""

from __future__ import annotations

import os
from typing import Any

import boto3
import httpx
import requests

from app.auth.jwt_auth import extract_org_id_from_jwt
from app.config import get_tracer_base_url
from app.integrations.clients.datadog.client import DatadogClient, DatadogConfig
from app.integrations.clients.tracer_client.client import TracerClient
from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.sentry import build_sentry_config, validate_sentry_config
from app.integrations.store import load_integrations
from app.nodes.resolve_integrations.node import (
    _classify_integrations,
    _load_env_integrations,
    _merge_local_integrations,
)

SUPPORTED_VERIFY_SERVICES = ("grafana", "datadog", "aws", "slack", "tracer", "github", "sentry")
CORE_VERIFY_SERVICES = frozenset({"grafana", "datadog", "aws"})
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
    for integration in env_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local env"
    for integration in store_integrations:
        service = str(integration.get("service", "")).strip().lower()
        if service:
            source_by_service[service] = "local store"

    effective: dict[str, dict[str, Any]] = {}
    for service in CORE_VERIFY_SERVICES:
        resolved_integration = classified_integrations.get(service)
        if isinstance(resolved_integration, dict):
            effective[service] = {
                "source": source_by_service.get(service, "local env"),
                "config": resolved_integration,
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
        effective["slack"] = {
            "source": "local env",
            "config": {"webhook_url": slack_webhook_url},
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

    return effective


def _verify_grafana(source: str, config: dict[str, Any]) -> dict[str, str]:
    endpoint = str(config.get("endpoint", "")).rstrip("/")
    api_key = str(config.get("api_key", "")).strip()
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
    datadog_client = DatadogClient(
        DatadogConfig(
            api_key=str(config.get("api_key", "")).strip(),
            app_key=str(config.get("app_key", "")).strip(),
            site=str(config.get("site", "datadoghq.com")).strip() or "datadoghq.com",
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


def _build_sts_client(config: dict[str, Any]) -> tuple[Any, str, str]:
    region = str(config.get("region", "us-east-1")).strip() or "us-east-1"
    role_arn = str(config.get("role_arn", "")).strip()
    external_id = str(config.get("external_id", "")).strip()
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

    credentials = config.get("credentials", {})
    return (
        boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=str(credentials.get("access_key_id", "")).strip(),
            aws_secret_access_key=str(credentials.get("secret_access_key", "")).strip(),
            aws_session_token=str(credentials.get("session_token", "")).strip() or None,
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
    webhook_url = str(config.get("webhook_url", "")).strip()
    if not webhook_url:
        return _result("slack", source, "missing", "SLACK_WEBHOOK_URL is not configured.")

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
    base_url = str(config.get("base_url", "")).strip() or get_tracer_base_url()
    jwt_token = str(config.get("jwt_token", "")).strip()
    if jwt_token.lower().startswith("bearer "):
        jwt_token = jwt_token.split(None, 1)[1].strip()
    if not jwt_token:
        return _result("tracer", source, "missing", "Missing JWT token for Tracer web app access.")

    org_id = extract_org_id_from_jwt(jwt_token)
    if not org_id:
        return _result("tracer", source, "failed", "JWT token does not contain an organization claim.")

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
                results.append(_result("slack", "-", "missing", "SLACK_WEBHOOK_URL is not configured."))
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
            results.append(_result(current_service, "-", "missing", "Not configured in local store or env."))
            continue

        source = str(integration["source"])
        config = dict(integration["config"])
        if current_service == "grafana":
            results.append(_verify_grafana(source, config))
        elif current_service == "datadog":
            results.append(_verify_datadog(source, config))
        elif current_service == "aws":
            results.append(_verify_aws(source, config))
        elif current_service == "tracer":
            results.append(_verify_tracer(source, config))
        elif current_service == "github":
            results.append(_verify_github(source, config))
        elif current_service == "sentry":
            results.append(_verify_sentry(source, config))

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
