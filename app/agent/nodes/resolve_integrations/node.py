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

# Mapping from integration service names to resolved_integrations keys
_SERVICE_KEY_MAP = {
    "Grafana": "grafana",
    "grafana": "grafana",
    "AWS": "aws",
    "aws": "aws",
    "Datadog": "datadog",
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
            "_all": [<raw integration records>]  # for extensibility
        }
    """
    resolved: dict[str, Any] = {}

    active = [i for i in integrations if i.get("status") == "active"]

    for integration in active:
        service = integration.get("service", "")

        if service.lower() in _SKIP_SERVICES:
            continue

        key = _SERVICE_KEY_MAP.get(service)
        if not key:
            # Store unknown services under their lowercase name for future extensibility
            key = service.lower()

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
            if role_arn:
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
            # Generic: store credentials under the service key
            resolved[key] = {
                "credentials": credentials,
                "integration_id": integration.get("id", ""),
            }

    resolved["_all"] = active
    return resolved


def _resolve_from_local_store(tracker: Any) -> dict:
    """Fall back to ~/.tracer/integrations.json when no auth context."""
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


@traceable(name="node_resolve_integrations")
def node_resolve_integrations(state: InvestigationState) -> dict:
    """Fetch all org integrations and classify them by service.

    Populates state["resolved_integrations"] with structured credentials
    so downstream nodes (detect_sources, plan_actions) can use them
    without making additional API calls.
    """
    tracker = get_tracker()
    tracker.start("resolve_integrations", "Fetching org integrations")

    org_id = state.get("org_id", "")
    auth_token = state.get("_auth_token", "")

    if not org_id or not auth_token:
        return _resolve_from_local_store(tracker)

    try:
        from app.agent.tools.clients.tracer_client import get_tracer_client_for_org

        client = get_tracer_client_for_org(org_id, auth_token)
        all_integrations = client.get_all_integrations()
    except Exception:
        tracker.complete(
            "resolve_integrations",
            fields_updated=["resolved_integrations"],
            message="Failed to fetch integrations",
        )
        return {"resolved_integrations": {}}

    resolved = _classify_integrations(all_integrations)
    services = [k for k in resolved if k != "_all"]

    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved integrations: {services}" if services else "No active integrations found",
    )

    return {"resolved_integrations": resolved}
