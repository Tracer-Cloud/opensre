"""
Ingestion layer for alert payloads.

Parses Grafana webhooks into InvestigationRequest objects.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.agent.output import debug_print

# ─────────────────────────────────────────────────────────────────────────────
# Grafana Alert Models
# ─────────────────────────────────────────────────────────────────────────────


class GrafanaAlertLabel(BaseModel):
    alertname: str
    severity: str = "warning"
    table: str | None = None
    environment: str = "production"
    pipeline_name: str | None = None
    run_id: str | None = None
    run_name: str | None = None


class GrafanaAlertAnnotation(BaseModel):
    summary: str
    description: str | None = None


class GrafanaAlert(BaseModel):
    status: str  # "firing" or "resolved"
    labels: GrafanaAlertLabel
    annotations: GrafanaAlertAnnotation
    startsAt: datetime
    fingerprint: str


class GrafanaAlertPayload(BaseModel):
    alerts: list[GrafanaAlert]
    title: str
    state: str
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Internal Request Object
# ─────────────────────────────────────────────────────────────────────────────

SEVERITY_MAP = {"critical": "critical", "high": "high", "warning": "warning", "info": "info"}
DEFAULT_SEVERITY = "warning"


@dataclass(frozen=True)
class InvestigationRequest:
    """Request object for the investigation agent."""

    alert_name: str
    pipeline_name: str
    severity: str
    environment: str
    summary: str | None
    raw_alert: dict[str, Any]


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────


def parse_grafana_payload(
    payload: dict[str, Any],
    default_table: str = "events_fact",
) -> InvestigationRequest:
    """Parse Grafana webhook into InvestigationRequest."""
    grafana = GrafanaAlertPayload(**payload)

    firing = [a for a in grafana.alerts if a.status == "firing"]
    if not firing:
        raise ValueError("No firing alerts in payload")

    alert = firing[0]
    debug_print(f"Parsed Grafana alert: {alert.labels.alertname}")
    raw_severity = alert.labels.severity.lower()

    return InvestigationRequest(
        alert_name=alert.labels.alertname,
        pipeline_name=alert.labels.table or default_table,
        severity=SEVERITY_MAP.get(raw_severity, DEFAULT_SEVERITY),
        environment=alert.labels.environment,
        summary=alert.annotations.summary,
        raw_alert=payload,
    )


def load_request_from_json(path: str | None) -> InvestigationRequest:
    """Load InvestigationRequest from JSON file or stdin."""
    if path in (None, "-"):
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_grafana_payload(payload)
