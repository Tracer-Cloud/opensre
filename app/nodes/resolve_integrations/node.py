"""Resolve integrations node - fetches org integrations and classifies by service.

Runs early in the investigation pipeline (after extract_alert) to make
integration credentials available for all downstream nodes. This replaces
per-node credential fetching with a single upfront resolution.
"""

import logging
import os
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from app.integrations.catalog import (
    classify_integrations as _classify_integrations,
)
from app.integrations.catalog import (
    load_env_integrations as _load_env_integrations,
)
from app.integrations.catalog import (
    merge_integrations_by_service as _merge_integrations_by_service,
)
from app.integrations.catalog import (
    merge_local_integrations as _merge_local_integrations,
)
from app.output import get_tracker
from app.state import InvestigationState

logger = logging.getLogger(__name__)


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


@traceable(name="node_resolve_integrations")
def node_resolve_integrations(
    state: InvestigationState,
    config: Optional[RunnableConfig] = None,  # noqa: UP007,UP045
) -> dict:
    """Fetch all org integrations and classify them by service.

    Priority:
      1. _auth_token from state (Slack webhook / inbound request) - remote API only, no local fallback
      2. JWT_TOKEN env var - remote API, with local store/env filling missing services
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

            all_integrations = get_tracer_client_for_org(
                org_id, webhook_token
            ).get_all_integrations()
        except Exception as exc:
            logger.warning("Remote integrations fetch failed: %s", exc)
            tracker.complete(
                "resolve_integrations",
                fields_updated=["resolved_integrations"],
                message="Remote integrations fetch failed",
            )
            return {"resolved_integrations": {}}
    else:
        env_token = _strip_bearer(os.getenv("JWT_TOKEN", "").strip())
        if env_token:
            if not org_id:
                org_id = _decode_org_id_from_token(env_token)
            if not org_id:
                return _resolve_from_local_sources(tracker)
            try:
                from app.services.tracer_client import get_tracer_client_for_org

                all_integrations = get_tracer_client_for_org(
                    org_id, env_token
                ).get_all_integrations()
            except Exception:
                logger.debug(
                    "Remote integrations fetch failed for org %s, falling back to local",
                    org_id,
                    exc_info=True,
                )
                return _resolve_from_local_sources(tracker)
            return _resolve_remote_with_local_fallback(all_integrations, tracker)
        return _resolve_from_local_sources(tracker)

    resolved = _classify_integrations(all_integrations)
    services = [service for service in resolved if service != "_all"]

    tracker.complete(
        "resolve_integrations",
        fields_updated=["resolved_integrations"],
        message=f"Resolved integrations: {services}"
        if services
        else "No active integrations found",
    )

    return {"resolved_integrations": resolved}


def _resolve_from_local_sources(tracker: Any) -> dict:
    from app.integrations.store import STORE_PATH, load_integrations

    store_integrations = load_integrations()
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
    services = [service for service in resolved if service != "_all"]
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
    services = [service for service in resolved if service != "_all"]

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
