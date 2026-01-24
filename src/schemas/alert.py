"""
Alert models for ingesting and normalizing Grafana alerts.

Code #3: Alert ingestion
A small snippet that receives a Grafana alert payload and normalizes it 
into a clean internal incident object.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class GrafanaAlertLabel(BaseModel):
    """Labels from a Grafana alert."""
    alertname: str
    severity: str = "warning"
    table: Optional[str] = None
    warehouse: Optional[str] = None
    environment: str = "production"


class GrafanaAlertAnnotation(BaseModel):
    """Annotations from a Grafana alert."""
    summary: str
    description: Optional[str] = None
    runbook_url: Optional[str] = None


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


class Alert(BaseModel):
    """
    Normalized internal alert model.
    
    This is the clean, tool-agnostic representation of an incident
    that the agent works with.
    """
    incident_id: str = Field(description="Unique identifier for the incident")
    alert_name: str = Field(description="Name of the alert")
    severity: str = Field(description="Alert severity: critical, warning, info")
    summary: str = Field(description="Human-readable summary of the issue")
    description: Optional[str] = Field(default=None, description="Detailed description")
    
    # What's affected
    affected_table: Optional[str] = Field(default=None, description="Database table if applicable")
    affected_system: Optional[str] = Field(default=None, description="System/service affected")
    environment: str = Field(default="production", description="Environment: production, staging, dev")
    
    # Timing
    detected_at: datetime = Field(description="When the alert was detected")
    
    # Source
    source: str = Field(default="grafana", description="Alert source system")
    source_url: Optional[str] = Field(default=None, description="URL to the alert in source system")
    runbook_url: Optional[str] = Field(default=None, description="URL to the runbook")


def normalize_grafana_alert(payload: GrafanaAlertPayload) -> Alert:
    """
    Normalize a Grafana alert payload into our internal Alert model.
    
    This is the alert ingestion function that proves the agent is driven
    by real production signals, not just a prompt.
    """
    # Take the first firing alert
    firing_alerts = [a for a in payload.alerts if a.status == "firing"]
    if not firing_alerts:
        raise ValueError("No firing alerts in payload")
    
    alert = firing_alerts[0]
    
    return Alert(
        incident_id=alert.fingerprint,
        alert_name=alert.labels.alertname,
        severity=alert.labels.severity,
        summary=alert.annotations.summary,
        description=alert.annotations.description,
        affected_table=alert.labels.table,
        affected_system=alert.labels.warehouse,
        environment=alert.labels.environment,
        detected_at=alert.startsAt,
        source="grafana",
        runbook_url=alert.annotations.runbook_url,
    )

