"""
Ingestion layer for alert payloads.

Parses, validates, and normalizes external alert formats into internal request objects.
Keeps parsing logic separate from CLI wiring and agent execution.
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────────────────────
# Grafana Alert Models (Pydantic validation)
# ─────────────────────────────────────────────────────────────────────────────

class GrafanaAlertLabel(BaseModel):
    """Labels from a Grafana alert."""
    alertname: str
    severity: str = "warning"
    table: str | None = None
    environment: str = "production"


class GrafanaAlertAnnotation(BaseModel):
    """Annotations from a Grafana alert."""
    summary: str
    description: str | None = None


class GrafanaAlert(BaseModel):
    """A single alert from Grafana."""
    status: str  # "firing" or "resolved"
    labels: GrafanaAlertLabel
    annotations: GrafanaAlertAnnotation
    startsAt: datetime
    fingerprint: str


class GrafanaAlertPayload(BaseModel):
    """The full Grafana webhook payload."""
    alerts: list[GrafanaAlert]
    title: str
    state: str
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Internal Request Object
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_AFFECTED_TABLE = "events_fact"


@dataclass(frozen=True)
class InvestigationRequest:
    """Internal request object for the investigation agent."""
    alert_name: str
    affected_table: str
    severity: str


# ─────────────────────────────────────────────────────────────────────────────
# Parsing Functions
# ─────────────────────────────────────────────────────────────────────────────

def parse_grafana_payload(payload: dict[str, Any]) -> InvestigationRequest:
    """
    Parse and validate a Grafana alert payload into an InvestigationRequest.

    Handles:
    - Pydantic validation of the payload structure
    - Extraction of fields needed for investigation
    - Default value for affected_table
    """
    grafana_payload = GrafanaAlertPayload(**payload)

    # Take the first firing alert
    firing_alerts = [a for a in grafana_payload.alerts if a.status == "firing"]
    if not firing_alerts:
        raise ValueError("No firing alerts in payload")

    alert = firing_alerts[0]

    return InvestigationRequest(
        alert_name=alert.labels.alertname,
        affected_table=alert.labels.table or DEFAULT_AFFECTED_TABLE,
        severity=alert.labels.severity,
    )


def load_request_from_json(path: str | None) -> InvestigationRequest:
    """
    Load an InvestigationRequest from a JSON file or stdin.

    Args:
        path: Path to JSON file, or None/"-" for stdin

    Returns:
        Validated InvestigationRequest ready for the agent
    """
    if path in (None, "-"):
        payload = json.load(sys.stdin)
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))

    return parse_grafana_payload(payload)

