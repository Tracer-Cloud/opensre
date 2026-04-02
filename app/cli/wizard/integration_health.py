"""Health checks for optional onboarding integrations."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from app.integrations.clients.datadog import DatadogClient, DatadogConfig
from app.integrations.clients.grafana import get_grafana_client_from_credentials
from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.models import (
    AWSIntegrationConfig,
    GrafanaIntegrationConfig,
    SlackWebhookConfig,
)
from app.integrations.sentry import build_sentry_config, validate_sentry_config


@dataclass(frozen=True)
class IntegrationHealthResult:
    """Result of validating an optional integration."""

    ok: bool
    detail: str


def validate_grafana_integration(*, endpoint: str, api_key: str) -> IntegrationHealthResult:
    """Validate Grafana credentials by discovering datasource UIDs."""
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate({"endpoint": endpoint, "api_key": api_key})
        client = get_grafana_client_from_credentials(
            endpoint=grafana_config.endpoint,
            api_key=grafana_config.api_key,
            account_id="opensre_onboard_probe",
        )
        discovered = client.discover_datasource_uids()
        if not discovered:
            return IntegrationHealthResult(
                ok=False,
                detail="Grafana is reachable, but no datasources could be discovered with this token.",
            )

        available = ", ".join(sorted(discovered))
        return IntegrationHealthResult(
            ok=True,
            detail=f"Grafana validated with datasource discovery: {available}.",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Grafana validation failed: {err}")


def validate_datadog_integration(*, api_key: str, app_key: str, site: str) -> IntegrationHealthResult:
    """Validate Datadog credentials with a monitor list request."""
    client = DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))
    result = client.list_monitors()
    if result.get("success"):
        return IntegrationHealthResult(
            ok=True,
            detail=f"Datadog validated against {site}; fetched {result.get('total', 0)} monitors.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Datadog validation failed: {result.get('error', 'unknown error')}",
    )


def validate_slack_webhook(*, webhook_url: str) -> IntegrationHealthResult:
    """Validate Slack webhook format and do a non-posting reachability probe."""
    try:
        slack_config = SlackWebhookConfig.model_validate({"webhook_url": webhook_url})
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    try:
        response = requests.get(slack_config.webhook_url, timeout=10, allow_redirects=False)
    except requests.RequestException as err:
        return IntegrationHealthResult(ok=False, detail=f"Slack webhook validation failed: {err}")

    if response.status_code == 404:
        return IntegrationHealthResult(ok=False, detail="Slack webhook returned 404; the URL looks invalid.")
    if response.status_code in {200, 400, 403, 405}:
        return IntegrationHealthResult(
            ok=True,
            detail=f"Slack webhook endpoint reachable (HTTP {response.status_code}) using a non-posting probe.",
        )
    return IntegrationHealthResult(
        ok=False,
        detail=f"Slack webhook probe returned unexpected HTTP {response.status_code}.",
    )


def validate_aws_integration(
    *,
    region: str,
    role_arn: str = "",
    external_id: str = "",
    access_key_id: str = "",
    secret_access_key: str = "",
    session_token: str = "",
) -> IntegrationHealthResult:
    """Validate AWS credentials with STS GetCallerIdentity."""
    try:
        import boto3
    except ImportError:
        return IntegrationHealthResult(ok=False, detail="AWS validation failed: boto3 is not installed.")

    try:
        aws_config = AWSIntegrationConfig.model_validate({
            "region": region,
            "role_arn": role_arn,
            "external_id": external_id,
            "credentials": (
                {
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                    "session_token": session_token,
                }
                if access_key_id or secret_access_key or session_token
                else None
            ),
        })
        if role_arn:
            sts = boto3.client("sts", region_name=aws_config.region)
            assume_kwargs: dict[str, str] = {
                "RoleArn": aws_config.role_arn,
                "RoleSessionName": "opensre-onboard-check",
            }
            if aws_config.external_id:
                assume_kwargs["ExternalId"] = aws_config.external_id
            creds = sts.assume_role(**assume_kwargs)["Credentials"]
            assumed = boto3.client(
                "sts",
                region_name=aws_config.region,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
            identity = assumed.get_caller_identity()
            return IntegrationHealthResult(
                ok=True,
                detail=f"AWS role validated for account {identity.get('Account')} as {identity.get('Arn')}.",
            )

        sts = boto3.client(
            "sts",
            region_name=aws_config.region,
            aws_access_key_id=aws_config.credentials.access_key_id if aws_config.credentials else "",
            aws_secret_access_key=aws_config.credentials.secret_access_key if aws_config.credentials else "",
            aws_session_token=(aws_config.credentials.session_token if aws_config.credentials else "") or None,
        )
        identity = sts.get_caller_identity()
        return IntegrationHealthResult(
            ok=True,
            detail=f"AWS credentials validated for account {identity.get('Account')} as {identity.get('Arn')}.",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"AWS validation failed: {err}")


def validate_github_mcp_integration(
    *,
    url: str = "",
    mode: str,
    auth_token: str = "",
    command: str = "",
    args: list[str] | None = None,
    toolsets: list[str] | None = None,
) -> IntegrationHealthResult:
    """Validate GitHub MCP connectivity and required repository tools."""
    config = build_github_mcp_config({
        "url": url,
        "mode": mode,
        "auth_token": auth_token,
        "command": command,
        "args": args or [],
        "toolsets": toolsets or [],
    })
    result = validate_github_mcp_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)


def validate_sentry_integration(
    *,
    base_url: str,
    organization_slug: str,
    auth_token: str,
    project_slug: str = "",
) -> IntegrationHealthResult:
    """Validate Sentry connectivity with an organization issues query."""
    config = build_sentry_config({
        "base_url": base_url,
        "organization_slug": organization_slug,
        "auth_token": auth_token,
        "project_slug": project_slug,
    })
    result = validate_sentry_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)
