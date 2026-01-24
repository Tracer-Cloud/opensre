"""
Report models for incident resolution output.

Code #2: Evidence-backed report
Models for assembling root cause, evidence, and recommended fix 
into actionable output.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from src.schemas.hypothesis import Hypothesis, Evidence


class RecommendedAction(BaseModel):
    """A recommended action to resolve the incident."""
    action: str = Field(description="What action to take")
    priority: str = Field(default="high", description="Priority: critical, high, medium, low")
    estimated_effort: Optional[str] = Field(default=None, description="Estimated time/effort")
    automated: bool = Field(default=False, description="Can this action be automated?")


class InvestigationTimeline(BaseModel):
    """Timeline of the investigation."""
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    steps_executed: int
    hypotheses_tested: int


class IncidentReport(BaseModel):
    """
    Complete incident resolution report.
    
    This is the final output that proves the agent produces
    actionable results, not just chat.
    """
    # Incident identification
    incident_id: str = Field(description="Unique identifier for the incident")
    alert_name: str = Field(description="Original alert name")
    severity: str = Field(description="Incident severity")
    
    # Summary
    title: str = Field(description="One-line title for the incident")
    summary: str = Field(description="Brief summary of what happened")
    
    # Root cause analysis
    root_cause: str = Field(description="The identified root cause")
    root_cause_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the root cause (0-1)"
    )
    
    # Evidence
    confirmed_hypothesis: Optional[Hypothesis] = Field(
        default=None,
        description="The hypothesis that was confirmed"
    )
    rejected_hypotheses: list[str] = Field(
        default_factory=list,
        description="List of hypotheses that were rejected"
    )
    evidence_summary: list[Evidence] = Field(
        default_factory=list,
        description="Key evidence that led to the conclusion"
    )
    
    # Impact
    affected_systems: list[str] = Field(
        default_factory=list,
        description="Systems affected by this incident"
    )
    impact_description: str = Field(description="Description of the impact")
    
    # Resolution
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list,
        description="Recommended actions to resolve"
    )
    
    # Timeline
    detected_at: datetime = Field(description="When the incident was detected")
    investigation_timeline: Optional[InvestigationTimeline] = Field(
        default=None,
        description="Timeline of the investigation"
    )
    
    # Metadata
    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this report was generated"
    )
    agent_version: str = Field(default="1.0.0", description="Agent version")

