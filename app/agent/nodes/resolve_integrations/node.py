"""Resolve integrations node - fetches org integrations and classifies by service.

Runs early in the investigation pipeline (after extract_alert) to make
integration credentials available for all downstream nodes. This replaces
per-node credential fetching with a single upfront resolution.
"""

from __future__ import annotations

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
    "aws": "aws",
    "eks": "aws",
    "amazon eks": "aws",
    "datadog": "datadog",
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

        if key == "grafana":
            endpoint = credentials.get("endpoint", "")
            api_key = credentials.get("api_key", "")
            if endpoint and api_key:
                resolved["grafana"] = {
                    "endpoint": endpoint,
                    "api_key": api_key,
                    "integration_id": integration.get("id", ""),
                }

        elif key == "aws":
            role_arn = integration.get("role_arn", "")
            external_id = integration.get("external_id", "")
            if role_arn and "aws" not in resolved:
                resolved["aws"] = {
                    "role_arn": role_arn,
                    "external_id": external_id,
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


@traceable(name="node_resolve_integrations")
def node_resolve_integrations(state: InvestigationState) -> dict:
    """Fetch all org integrations and classify them by service.

    Priority:
      1. _auth_token from state (Slack webhook / inbound request) — remote API only, no local fallback
      2. JWT_TOKEN env var — remote API, falls back to local store on failure
      3. Local integrations store (~/.tracer/integrations.json)
    """
    import logging
    import os

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
                return _resolve_from_local_store(tracker)
            try:
                from app.agent.tools.clients.tracer_client import get_tracer_client_for_org
                all_integrations = get_tracer_client_for_org(org_id, env_token).get_all_integrations()
            except Exception:
                return _resolve_from_local_store(tracker)
        else:
            # Priority 3: local store only
            return _resolve_from_local_store(tracker)

    resolved = _classify_integrations(all_integrations)
    services = [k for k in resolved if k != "_all"]

    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved integrations: {services}" if services else "No active integrations found",
    )

    return {"resolved_integrations": resolved}


def _resolve_from_local_store(tracker: Any) -> dict:
    from app.integrations.store import STORE_PATH, load_integrations

    integrations = load_integrations()
    if not integrations:
        tracker.complete(
            "resolve_integrations",
            fields_updated=["resolved_integrations"],
            message=f"No auth context and no local integrations found (store: {STORE_PATH})",
        )
        return {"resolved_integrations": {}}

    resolved = _classify_integrations(integrations)
    services = [k for k in resolved if k != "_all"]
    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved local integrations: {services}",
    )
    return {"resolved_integrations": resolved}
