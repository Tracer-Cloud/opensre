"""Data contracts for the frame_problem node."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.agent.state import InvestigationState


class AlertExtractionInput(BaseModel):
    """Normalized input for alert extraction."""

    raw_alert: str = Field(description="Raw alert payload as a string")


class AlertDetails(BaseModel):
    """Structured alert details extracted from raw input."""

    alert_name: str = Field(description="Name of the alert")
    affected_table: str = Field(description="Primary affected table")
    severity: str = Field(description="Severity of the alert (e.g. critical, high, warning, info)")
    environment: str | None = Field(default=None, description="Environment, if present")
    summary: str | None = Field(default=None, description="Short alert summary, if present")


class ProblemStatementInput(BaseModel):
    """Structured input for the problem statement LLM call."""

    alert_name: str = Field(description="Name of the alert")
    affected_table: str = Field(description="Primary affected table")
    severity: str = Field(description="Severity of the alert")

    @classmethod
    def from_state(cls, state: InvestigationState) -> ProblemStatementInput:
        return cls(
            alert_name=state.get("alert_name", "Unknown"),
            affected_table=state.get("affected_table", "Unknown"),
            severity=state.get("severity", "Unknown"),
        )


class ProblemStatement(BaseModel):
    """Structured problem statement for the investigation."""

    summary: str = Field(description="One-line summary of the problem")
    context: str = Field(description="Background context about the alert and affected systems")
    investigation_goals: list[str] = Field(description="Specific goals for the investigation")
    constraints: list[str] = Field(description="Known constraints or limitations")


class ContextSourceError(BaseModel):
    """Structured error from a context source."""

    source: str = Field(description="Context source name")
    message: str = Field(description="Error message for the source failure")


class TracerWebRunContext(BaseModel):
    """Context gathered from Tracer Web App."""

    model_config = ConfigDict(extra="allow")

    found: bool
    error: str | None = None
    pipeline_name: str | None = None
    run_id: str | None = None
    run_name: str | None = None
    trace_id: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    run_cost: float | None = None
    tool_count: int | None = None
    user_email: str | None = None
    instance_type: str | None = None
    region: str | None = None
    log_file_count: int | None = None
    run_url: str | None = None
    pipelines_checked: int | None = None


class ContextEvidence(BaseModel):
    """Typed wrapper for evidence gathered before runtime investigation."""

    model_config = ConfigDict(extra="allow")

    tracer_web_run: TracerWebRunContext | None = None
    context_errors: list[ContextSourceError] = Field(default_factory=list)

    def to_state(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_defaults=True)
