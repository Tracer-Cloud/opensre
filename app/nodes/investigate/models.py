"""Pydantic models for investigate node inputs and outputs."""

from typing import Any

from pydantic import BaseModel, Field

from app.state import InvestigationState


class InvestigateInput(BaseModel):
    """Input data for the investigate node."""

    raw_alert: dict[str, Any] | str = Field(description="Raw alert payload")
    context: dict[str, Any] = Field(default_factory=dict, description="Investigation context")
    problem_md: str = Field(default="", description="Problem statement markdown")
    alert_name: str = Field(default="", description="Alert name")
    investigation_recommendations: list[str] = Field(
        default_factory=list, description="Recommendations from previous analysis"
    )
    executed_hypotheses: list[dict[str, Any]] = Field(
        default_factory=list, description="History of executed hypotheses"
    )
    evidence: dict[str, Any] = Field(default_factory=dict, description="Current evidence")
    investigation_loop_count: int = Field(default=0, description="Number of investigation loops")

    @classmethod
    def from_state(cls, state: InvestigationState | dict[str, Any]) -> "InvestigateInput":
        """Create InvestigateInput from investigation state."""
        return cls(
            raw_alert=state.get("raw_alert", {}),
            context=state.get("context", {}),
            problem_md=state.get("problem_md", ""),
            alert_name=state.get("alert_name", ""),
            investigation_recommendations=state.get("investigation_recommendations", []),
            executed_hypotheses=state.get("executed_hypotheses", []),
            evidence=state.get("evidence", {}),
            investigation_loop_count=state.get("investigation_loop_count", 0),
        )


class InvestigateOutput(BaseModel):
    """Output data from the investigate node."""

    evidence: dict[str, Any] = Field(description="Updated evidence dictionary")
    executed_hypotheses: list[dict[str, Any]] = Field(
        description="Updated executed hypotheses list"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for state update."""
        return {
            "evidence": self.evidence,
            "executed_hypotheses": self.executed_hypotheses,
        }
