"""Models for frame_problem statement."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent.state import InvestigationState


class ProblemStatementInput(BaseModel):
    """Structured input for the problem statement LLM call."""

    alert_name: str = Field(description="Name of the alert")
    pipeline_name: str = Field(description="Primary affected table")
    severity: str = Field(description="Severity of the alert")

    @classmethod
    def from_state(cls, state: InvestigationState) -> ProblemStatementInput:
        return cls(
            alert_name=state.get("alert_name", "Unknown"),
            pipeline_name=state.get("pipeline_name", "Unknown"),
            severity=state.get("severity", "Unknown"),
        )


class ProblemStatement(BaseModel):
    """Structured problem statement for the investigation."""

    summary: str = Field(description="One-line summary of the problem")
    context: str = Field(description="Background context about the alert and affected systems")
    investigation_goals: list[str] = Field(description="Specific goals for the investigation")
    constraints: list[str] = Field(description="Known constraints or limitations")
