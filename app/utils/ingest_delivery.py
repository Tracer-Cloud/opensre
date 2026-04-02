"""Send investigation results to the Tracer webapp ingest endpoint."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.config import get_tracer_base_url
from app.state import InvestigationState

logger = logging.getLogger(__name__)


def _normalize_severity(severity: str | None) -> str:
    level = (severity or "").lower()
    if level in {"critical", "high", "warning", "info"}:
        return level
    return "info"


def _resolve_source(state: InvestigationState) -> str:
    raw_alert = state.get("raw_alert") or {}
    if isinstance(raw_alert, dict) and raw_alert.get("source"):
        return str(raw_alert.get("source"))
    slack_ctx = state.get("slack_context") or {}
    if slack_ctx.get("team_id"):
        return "slack"
    return "tracer"


def _resolve_thread_id(state: InvestigationState) -> str:
    thread_id = state.get("thread_id") or ""
    if thread_id:
        return thread_id
    slack_ctx = state.get("slack_context") or {}
    fallback = slack_ctx.get("thread_ts") or slack_ctx.get("ts") or ""
    if fallback:
        return fallback
    return state.get("run_id") or ""


def build_ingest_payload(state: InvestigationState) -> dict[str, Any]:
    raw_alert = state.get("raw_alert") if isinstance(state.get("raw_alert"), dict) else {}
    # Fill missing fingerprint with a stable per-incident id (thread_id/run_id/alert_id)
    if isinstance(raw_alert, dict) and not raw_alert.get("fingerprint"):
        fingerprint = (
            state.get("thread_id")
            or state.get("run_id")
            or raw_alert.get("alert_id")
            or raw_alert.get("id")
        )
        if fingerprint:
            raw_alert["fingerprint"] = fingerprint
    planned_actions = state.get("planned_actions") or []

    investigation_output = {
        "org_id": state.get("org_id"),
        "alert_name": state.get("alert_name"),
        "pipeline_name": state.get("pipeline_name") or "",
        "severity": _normalize_severity(state.get("severity")),
        "summary": state.get("summary") or state.get("problem_md") or state.get("root_cause") or state.get("alert_name"),
        "raw_alert": raw_alert,
        "root_cause": state.get("root_cause") or "",
        "confidence": state.get("validity_score") or 0,
        "validity_score": state.get("validity_score") or 0,
        "planned_actions": planned_actions,
        "problem_md": state.get("problem_md") or "",
        "investigation_recommendations": state.get("investigation_recommendations") or [],
    }

    # Attach full report if provided
    if state.get("problem_report"):
        investigation_output["problem_report"] = state.get("problem_report")

    metadata = {
        "source": _resolve_source(state),
        "investigation_type": "auto",
        "connection_type": "platform",
        "alert_fired_at": raw_alert.get("fired_at") if isinstance(raw_alert, dict) else None,
        "thread_id": _resolve_thread_id(state),
        "run_id": state.get("run_id") or "",
    }

    return {"investigation_output": investigation_output, "metadata": metadata}


def send_ingest(state: InvestigationState) -> str | None:
    """Deliver investigation to the ingest API.

    Returns the investigation ID from the API response, or None on failure.
    """
    token = os.getenv("TRACER_INGEST_TOKEN")
    base_url = os.getenv("TRACER_API_URL") or get_tracer_base_url()

    if not token:
        logger.debug("[ingest] TRACER_INGEST_TOKEN not set; skipping ingest.")
        return None

    api_url = f"{base_url.rstrip('/')}/api/investigations/ingest"
    payload = build_ingest_payload(state)

    # thread_id is required for idempotent updates; skip if missing
    if not payload["metadata"].get("thread_id"):
        logger.debug("[ingest] Missing thread_id; skipping ingest.")
        return None

    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = httpx.post(api_url, json=payload, headers=headers, timeout=10.0)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        investigation_id: str | None = (data.get("data") or {}).get("investigation_id")
        logger.debug(
            "[ingest] Delivered investigation ingest (thread_id=%s, id=%s)",
            payload["metadata"]["thread_id"],
            investigation_id,
        )
        return investigation_id
    except httpx.HTTPStatusError as exc:  # noqa: BLE001
        detail = exc.response.text[:200] if exc.response is not None else str(exc)
        logger.warning(
            "[ingest] Delivery HTTP failure status=%s thread_id=%s url=%s response_snippet=%s",
            exc.response.status_code if exc.response else "unknown",
            payload["metadata"].get("thread_id"),
            api_url,
            detail,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ingest] Delivery failed: %s", exc)
    return None
