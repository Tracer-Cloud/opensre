"""Health checks for optional onboarding integrations."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from app.integrations.github_mcp import build_github_mcp_config, validate_github_mcp_config
from app.integrations.gitlab import build_gitlab_config, validate_gitlab_config
from app.integrations.models import (
    AWSIntegrationConfig,
    CoralogixIntegrationConfig,
    GoogleDocsIntegrationConfig,
    GrafanaIntegrationConfig,
    HoneycombIntegrationConfig,
    SlackWebhookConfig,
)
from app.integrations.sentry import build_sentry_config, validate_sentry_config
from app.services.coralogix import CoralogixClient
from app.services.datadog import DatadogClient, DatadogConfig
from app.services.grafana import get_grafana_client_from_credentials
from app.services.honeycomb import HoneycombClient
from app.services.opsgenie import OpsGenieClient, OpsGenieConfig
from app.services.vercel import VercelClient, VercelConfig


@dataclass(frozen=True)
class IntegrationHealthResult:
    """Result of validating an optional integration."""

    ok: bool
    detail: str


def validate_grafana_integration(*, endpoint: str, api_key: str) -> IntegrationHealthResult:
    """Validate Grafana credentials by discovering datasource UIDs."""
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate(
            {"endpoint": endpoint, "api_key": api_key}
        )
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


def validate_datadog_integration(
    *, api_key: str, app_key: str, site: str
) -> IntegrationHealthResult:
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


def validate_honeycomb_integration(
    *,
    api_key: str,
    dataset: str,
    base_url: str,
) -> IntegrationHealthResult:
    """Validate Honeycomb credentials with auth and a lightweight query."""
    try:
        honeycomb_config = HoneycombIntegrationConfig.model_validate(
            {
                "api_key": api_key,
                "dataset": dataset,
                "base_url": base_url,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    client = HoneycombClient(honeycomb_config)
    auth_result = client.validate_access()
    if not auth_result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Honeycomb auth failed: {auth_result.get('error', 'unknown error')}",
        )

    query_result = client.run_query(
        {"calculations": [{"op": "COUNT"}], "time_range": 900},
        limit=1,
    )
    if not query_result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Honeycomb query failed: {query_result.get('error', 'unknown error')}",
        )

    return IntegrationHealthResult(
        ok=True,
        detail=(
            f"Honeycomb validated against dataset {honeycomb_config.dataset} "
            f"at {honeycomb_config.base_url}."
        ),
    )


def validate_coralogix_integration(
    *,
    api_key: str,
    base_url: str,
    application_name: str = "",
    subsystem_name: str = "",
) -> IntegrationHealthResult:
    """Validate Coralogix access with a lightweight DataPrime query."""
    try:
        coralogix_config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": api_key,
                "base_url": base_url,
                "application_name": application_name,
                "subsystem_name": subsystem_name,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    client = CoralogixClient(coralogix_config)
    result = client.validate_access()
    if not result.get("success"):
        return IntegrationHealthResult(
            ok=False,
            detail=f"Coralogix validation failed: {result.get('error', 'unknown error')}",
        )

    scope: list[str] = []
    if coralogix_config.application_name:
        scope.append(f"application {coralogix_config.application_name}")
    if coralogix_config.subsystem_name:
        scope.append(f"subsystem {coralogix_config.subsystem_name}")
    scope_suffix = f" ({', '.join(scope)})" if scope else ""
    return IntegrationHealthResult(
        ok=True,
        detail=(
            f"Coralogix validated against {coralogix_config.base_url}{scope_suffix}; "
            f"DataPrime returned {result.get('total', 0)} row(s)."
        ),
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
        return IntegrationHealthResult(
            ok=False, detail="Slack webhook returned 404; the URL looks invalid."
        )
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
        return IntegrationHealthResult(
            ok=False, detail="AWS validation failed: boto3 is not installed."
        )

    try:
        aws_config = AWSIntegrationConfig.model_validate(
            {
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
            }
        )
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
            aws_access_key_id=aws_config.credentials.access_key_id
            if aws_config.credentials
            else "",
            aws_secret_access_key=aws_config.credentials.secret_access_key
            if aws_config.credentials
            else "",
            aws_session_token=(
                aws_config.credentials.session_token if aws_config.credentials else ""
            )
            or None,
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
    config = build_github_mcp_config(
        {
            "url": url,
            "mode": mode,
            "auth_token": auth_token,
            "command": command,
            "args": args or [],
            "toolsets": toolsets or [],
        }
    )
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
    config = build_sentry_config(
        {
            "base_url": base_url,
            "organization_slug": organization_slug,
            "auth_token": auth_token,
            "project_slug": project_slug,
        }
    )
    result = validate_sentry_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)


def validate_gitlab_integration(
    *,
    base_url: str,
    auth_token: str,
) -> IntegrationHealthResult:
    """Validate Gitlab connectivity with an users api."""
    config = build_gitlab_config(
        {
            "base_url": base_url,
            "auth_token": auth_token
        }
    )
    result = validate_gitlab_config(config)
    return IntegrationHealthResult(ok=result.ok, detail=result.detail)

def validate_google_docs_integration(
    *,
    credentials_file: str,
    folder_id: str,
) -> IntegrationHealthResult:
    """Validate Google Docs credentials and folder access."""
    from pathlib import Path

    from app.services.google_docs import GoogleDocsClient

    try:
        config = GoogleDocsIntegrationConfig.model_validate(
            {
                "credentials_file": credentials_file,
                "folder_id": folder_id,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    if not config.credentials_file or not config.folder_id:
        return IntegrationHealthResult(ok=False, detail="Missing credentials_file or folder_id.")

    if not Path(config.credentials_file).exists():
        return IntegrationHealthResult(
            ok=False, detail=f"Credentials file not found: {config.credentials_file}"
        )

    try:
        client = GoogleDocsClient(config)
        result = client.validate_access()
    except Exception as exc:
        return IntegrationHealthResult(ok=False, detail=f"Google API validation failed: {exc}")

    if not result.get("success"):
        return IntegrationHealthResult(
            ok=False, detail=f"Folder access check failed: {result.get('error', 'unknown error')}"
        )

    return IntegrationHealthResult(
        ok=True,
        detail=f"Connected to Drive folder {config.folder_id} ({result.get('file_count', 0)} items).",
    )


def validate_vercel_integration(*, api_token: str, team_id: str = "") -> IntegrationHealthResult:
    """Validate Vercel credentials by listing accessible projects."""
    if not api_token:
        return IntegrationHealthResult(ok=False, detail="Vercel API token is required.")
    try:
        with VercelClient(VercelConfig(api_token=api_token, team_id=team_id)) as client:
            result = client.list_projects()
        if result.get("success"):
            return IntegrationHealthResult(
                ok=True,
                detail=f"Vercel validated; listed {result.get('total', 0)} project(s).",
            )
        return IntegrationHealthResult(
            ok=False,
            detail=f"Vercel validation failed: {result.get('error', 'unknown error')}",
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=f"Vercel validation failed: {err}")


def validate_jira_integration(*, base_url: str, email: str, api_token: str, project_key: str) -> IntegrationHealthResult:
    """Validate Jira connectivity and project key accessibility."""
    import httpx

    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/rest/api/3/myself",
            auth=(email, api_token),
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            display = data.get("displayName") or data.get("emailAddress") or email

            project_resp = httpx.get(
                f"{base_url.rstrip('/')}/rest/api/3/project/{project_key}",
                auth=(email, api_token),
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if project_resp.status_code == 404:
                return IntegrationHealthResult(ok=False, detail=f"Project '{project_key}' not found. Check the project key.")
            if project_resp.status_code != 200:
                return IntegrationHealthResult(ok=False, detail=f"Could not verify project '{project_key}': HTTP {project_resp.status_code}.")

            return IntegrationHealthResult(ok=True, detail=f"Jira connected as {display}, project '{project_key}' verified.")
        if resp.status_code == 401:
            return IntegrationHealthResult(ok=False, detail="Jira credentials invalid. Check email and API token.")
        if resp.status_code == 404:
            return IntegrationHealthResult(ok=False, detail="Jira base URL not found. Check the URL.")
        return IntegrationHealthResult(ok=False, detail=f"Jira returned unexpected status {resp.status_code}.")
    except Exception as e:
        return IntegrationHealthResult(ok=False, detail=f"Jira validation failed: {e}")


def validate_opsgenie_integration(
    *,
    api_key: str,
    region: str = "us",
) -> IntegrationHealthResult:
    """Validate OpsGenie connectivity by listing alerts."""
    if not api_key:
        return IntegrationHealthResult(ok=False, detail="OpsGenie API key is required.")
    try:
        config = OpsGenieConfig(api_key=api_key, region=region)
        with OpsGenieClient(config) as client:
            result = client.list_alerts(limit=1)
        if result.get("success"):
            return IntegrationHealthResult(
                ok=True,
                detail=f"OpsGenie validated ({config.region.upper()} region); API key accepted.",
            )
        return IntegrationHealthResult(
            ok=False,
            detail=f"OpsGenie validation failed: {result.get('error', 'unknown error')}",
        )
    except Exception as err:
        return IntegrationHealthResult(
            ok=False,
            detail=f"OpsGenie validation failed: {err}",
        )
