"""Models for frame_problem extract."""

from pydantic import BaseModel, Field


class AlertExtractionInput(BaseModel):
    """Normalized input for alert extraction."""

    raw_alert: str = Field(description="Raw alert payload as a string")


class AlertDetails(BaseModel):
    """Structured alert details extracted from raw input."""

    alert_name: str = Field(description="Name of the alert")
    pipeline_name: str = Field(description="Primary affected table")
    severity: str = Field(description="Severity of the alert (e.g. critical, high, warning, info)")
    environment: str | None = Field(default=None, description="Environment, if present")
    summary: str | None = Field(default=None, description="Short alert summary, if present")
