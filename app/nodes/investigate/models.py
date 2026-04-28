"""Pydantic models for investigate node inputs and outputs."""

from typing import cast

from pydantic import BaseModel, Field

from app.nodes.investigate.types import ExecutedHypothesis
from app.state import InvestigationState


def _string_value(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _int_value(value: object, default: int) -> int:
    return value if isinstance(value, int) else default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _object_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _raw_alert(value: object) -> dict[str, object] | str:
    if isinstance(value, (dict, str)):
        return value
    return {}


def _executed_hypotheses_from_state(value: object) -> list[ExecutedHypothesis]:
    if not isinstance(value, list):
        return []
    return [cast(ExecutedHypothesis, entry) for entry in value if isinstance(entry, dict)]


class InvestigateInput(BaseModel):
    """Input data for the investigate node."""

    raw_alert: dict[str, object] | str = Field(description="Raw alert payload")
    context: dict[str, object] = Field(default_factory=dict, description="Investigation context")
    problem_md: str = Field(default="", description="Problem statement markdown")
    alert_name: str = Field(default="", description="Alert name")
    investigation_recommendations: list[str] = Field(
        default_factory=list, description="Recommendations from previous analysis"
    )
    executed_hypotheses: list[ExecutedHypothesis] = Field(
        default_factory=list, description="History of executed hypotheses"
    )
    evidence: dict[str, object] = Field(default_factory=dict, description="Current evidence")
    investigation_loop_count: int = Field(default=0, description="Number of investigation loops")
    tool_budget: int = Field(default=10, ge=1, le=50, description="Maximum tools per step")
    incident_window: dict[str, object] | None = Field(
        default=None,
        description=(
            "Resolved incident time window (from app.incident_window). Threaded "
            "through plan_actions and exposed to opt-in tools via "
            "available_sources['_meta']['incident_window']. None when "
            "extract_alert has not yet populated state.incident_window."
        ),
    )

    @classmethod
    def from_state(cls, state: InvestigationState | dict[str, object]) -> "InvestigateInput":
        """Create InvestigateInput from investigation state."""
        raw_window = state.get("incident_window")
        incident_window = raw_window if isinstance(raw_window, dict) else None
        return cls(
            raw_alert=_raw_alert(state.get("raw_alert", {})),
            context=_object_dict(state.get("context", {})),
            problem_md=_string_value(state.get("problem_md", "")),
            alert_name=_string_value(state.get("alert_name", "")),
            investigation_recommendations=_string_list(
                state.get("investigation_recommendations", [])
            ),
            executed_hypotheses=_executed_hypotheses_from_state(
                state.get("executed_hypotheses", [])
            ),
            evidence=_object_dict(state.get("evidence", {})),
            investigation_loop_count=_int_value(state.get("investigation_loop_count", 0), 0),
            tool_budget=_int_value(state.get("tool_budget", 10), 10),
            incident_window=incident_window,
        )


class InvestigateOutput(BaseModel):
    """Output data from the investigate node."""

    evidence: dict[str, object] = Field(description="Updated evidence dictionary")
    executed_hypotheses: list[ExecutedHypothesis] = Field(
        description="Updated executed hypotheses list"
    )

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for state update."""
        return {
            "evidence": self.evidence,
            "executed_hypotheses": self.executed_hypotheses,
        }
