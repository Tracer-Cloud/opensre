"""Resolve integrations node - fetches org integrations and classifies by service.

Runs early in the investigation pipeline (after extract_alert) to make
integration credentials available for all downstream nodes. This replaces
per-node credential fetching with a single upfront resolution.
"""

from __future__ import annotations

import os
from typing import Any

from langsmith import traceable

from app.agent.output import get_tracker
from app.agent.state import InvestigationState

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
    "github": "github",
    "github_mcp": "github",
    "sentry": "sentry",
}


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
        service = integration.get("service", "")

        if service.lower() in _SKIP_SERVICES:
            continue

        key = _SERVICE_KEY_MAP.get(service.lower(), service.lower())
        credentials = integration.get("credentials", {})

        if key in ("grafana", "grafana_local"):
            from urllib.parse import urlparse as _urlparse
            endpoint = credentials.get("endpoint", "")
            api_key = credentials.get("api_key", "")
            if not endpoint:
                continue
            host = _urlparse(endpoint).hostname or ""
            is_local = host in {"localhost", "127.0.0.1", "0.0.0.0"}
            if is_local:
                # Always treat localhost Grafana as grafana_local (Loki only, anonymous auth)
                resolved["grafana_local"] = {
                    "endpoint": endpoint,
                    "api_key": "",
                    "integration_id": integration.get("id", ""),
                }
            elif api_key and api_key != "local":
                resolved["grafana"] = {
                    "endpoint": endpoint,
                    "api_key": api_key,
                    "integration_id": integration.get("id", ""),
                }

        elif key == "aws":
            role_arn = integration.get("role_arn", "")
            external_id = integration.get("external_id", "")
            region = credentials.get("region", "us-east-1")
            access_key_id = credentials.get("access_key_id", "")
            secret_access_key = credentials.get("secret_access_key", "")
            session_token = credentials.get("session_token", "")
            if role_arn and "aws" not in resolved:
                resolved["aws"] = {
                    "role_arn": role_arn,
                    "external_id": external_id,
                    "region": region,
                    "integration_id": integration.get("id", ""),
                }
            elif access_key_id and secret_access_key and "aws" not in resolved:
                resolved["aws"] = {
                    "region": region,
                    "credentials": {
                        "access_key_id": access_key_id,
                        "secret_access_key": secret_access_key,
                        "session_token": session_token,
                    },
                    "integration_id": integration.get("id", ""),
                }

        elif key == "datadog":
            api_key = credentials.get("api_key", "")
            app_key = credentials.get("app_key", "")
            site = credentials.get("site", "datadoghq.com")
            if api_key and app_key:
                resolved["datadog"] = {
                    "api_key": api_key,
                    "app_key": app_key,
                    "site": site,
                    "integration_id": integration.get("id", ""),
                }

        elif key == "github":
            url = credentials.get("url", "")
            mode = credentials.get("mode", "streamable-http")
            command = credentials.get("command", "")
            args = credentials.get("args", [])
            auth_token = credentials.get("auth_token", "")
            toolsets = credentials.get("toolsets", [])
            if (url and mode != "stdio") or (mode == "stdio" and command):
                resolved["github"] = {
                    "url": url,
                    "mode": mode,
                    "command": command,
                    "args": args,
                    "auth_token": auth_token,
                    "toolsets": toolsets,
                    "integration_id": integration.get("id", ""),
                }

        elif key == "sentry":
            base_url = credentials.get("base_url", "https://sentry.io")
            organization_slug = credentials.get("organization_slug", "")
            auth_token = credentials.get("auth_token", "")
            project_slug = credentials.get("project_slug", "")
            if organization_slug and auth_token:
                resolved["sentry"] = {
                    "base_url": base_url,
                    "organization_slug": organization_slug,
                    "auth_token": auth_token,
                    "project_slug": project_slug,
                    "integration_id": integration.get("id", ""),
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
        integrations.append({
            "id": "env-grafana",
            "service": "grafana",
            "status": "active",
            "credentials": {
                "endpoint": grafana_endpoint,
                "api_key": grafana_api_key,
            },
        })

    datadog_api_key = os.getenv("DD_API_KEY", "").strip()
    datadog_app_key = os.getenv("DD_APP_KEY", "").strip()
    datadog_site = os.getenv("DD_SITE", "datadoghq.com").strip() or "datadoghq.com"
    if datadog_api_key and datadog_app_key:
        integrations.append({
            "id": "env-datadog",
            "service": "datadog",
            "status": "active",
            "credentials": {
                "api_key": datadog_api_key,
                "app_key": datadog_app_key,
                "site": datadog_site,
            },
        })

    aws_role_arn = os.getenv("AWS_ROLE_ARN", "").strip()
    aws_external_id = os.getenv("AWS_EXTERNAL_ID", "").strip()
    aws_region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
    aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    aws_session_token = os.getenv("AWS_SESSION_TOKEN", "").strip()
    if aws_role_arn:
        integrations.append({
            "id": "env-aws",
            "service": "aws",
            "status": "active",
            "role_arn": aws_role_arn,
            "external_id": aws_external_id,
            "credentials": {"region": aws_region},
        })
    elif aws_access_key_id and aws_secret_access_key:
        integrations.append({
            "id": "env-aws",
            "service": "aws",
            "status": "active",
            "credentials": {
                "access_key_id": aws_access_key_id,
                "secret_access_key": aws_secret_access_key,
                "session_token": aws_session_token,
                "region": aws_region,
            },
        })

    github_mode = os.getenv("GITHUB_MCP_MODE", "streamable-http").strip() or "streamable-http"
    github_url = os.getenv("GITHUB_MCP_URL", "").strip()
    github_command = os.getenv("GITHUB_MCP_COMMAND", "").strip()
    github_args = os.getenv("GITHUB_MCP_ARGS", "").strip()
    github_auth_token = os.getenv("GITHUB_MCP_AUTH_TOKEN", "").strip()
    github_toolsets = os.getenv("GITHUB_MCP_TOOLSETS", "").strip()
    if (github_mode == "stdio" and github_command) or (github_mode != "stdio" and github_url):
        integrations.append({
            "id": "env-github",
            "service": "github",
            "status": "active",
            "credentials": {
                "url": github_url,
                "mode": github_mode,
                "command": github_command,
                "args": [part for part in github_args.split() if part],
                "auth_token": github_auth_token,
                "toolsets": [part.strip() for part in github_toolsets.split(",") if part.strip()],
            },
        })

    sentry_org_slug = os.getenv("SENTRY_ORG_SLUG", "").strip()
    sentry_auth_token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    if sentry_org_slug and sentry_auth_token:
        integrations.append({
            "id": "env-sentry",
            "service": "sentry",
            "status": "active",
            "credentials": {
                "base_url": os.getenv("SENTRY_URL", "https://sentry.io").strip() or "https://sentry.io",
                "organization_slug": sentry_org_slug,
                "auth_token": sentry_auth_token,
                "project_slug": os.getenv("SENTRY_PROJECT_SLUG", "").strip(),
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
def node_resolve_integrations(state: InvestigationState) -> dict:
    """Fetch all org integrations and classify them by service.

    Priority:
      1. _auth_token from state (Slack webhook / inbound request) — remote API only, no local fallback
      2. JWT_TOKEN env var — remote API, with local store/env filling missing services
      3. Local sources: ~/.tracer/integrations.json, plus env-based integrations for standalone use
    """
    import logging

    tracker = get_tracker()
    tracker.start("resolve_integrations", "Fetching org integrations")

    log = logging.getLogger(__name__)
    org_id = state.get("org_id", "")

    webhook_token = _strip_bearer(state.get("_auth_token", "").strip())
    if webhook_token:
        if not org_id:
            org_id = _decode_org_id_from_token(webhook_token)
        if not org_id:
            log.warning("_auth_token present but could not decode org_id")
            tracker.complete(
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
                message="Auth token present but org_id could not be determined",
            )
            return {"resolved_integrations": {}}
        try:
            from app.agent.tools.clients.tracer_client import get_tracer_client_for_org
            all_integrations = get_tracer_client_for_org(org_id, webhook_token).get_all_integrations()
        except Exception as exc:
            log.warning("Remote integrations fetch failed: %s", exc)
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
                from app.agent.tools.clients.tracer_client import get_tracer_client_for_org
                all_integrations = get_tracer_client_for_org(org_id, env_token).get_all_integrations()
            except Exception:
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
