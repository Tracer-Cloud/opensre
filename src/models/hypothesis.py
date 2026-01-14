"""
Hypothesis models for structured investigation.

Code #5: Hypothesis model
A Pydantic schema for hypotheses and results that proves the agent 
is not free-text guessing but working in a structured way.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class HypothesisStatus(str, Enum):
    """Status of a hypothesis in the investigation."""
    PENDING = "pending"      # Not yet tested
    TESTING = "testing"      # Currently being tested
    CONFIRMED = "confirmed"  # Evidence supports this hypothesis
    REJECTED = "rejected"    # Evidence contradicts this hypothesis
    INCONCLUSIVE = "inconclusive"  # Not enough evidence either way


class Evidence(BaseModel):
    """A piece of evidence collected during investigation."""
    source: str = Field(description="Where the evidence came from (e.g., 's3', 'nextflow')")
    tool_used: str = Field(description="The tool/API call that collected this evidence")
    finding: str = Field(description="What was found")
    supports_hypothesis: bool = Field(description="Does this support or contradict the hypothesis?")
    raw_data: Optional[dict] = Field(default=None, description="Raw data from the API call")
    timestamp: Optional[str] = Field(default=None, description="When the evidence was collected")


class Hypothesis(BaseModel):
    """
    A hypothesis about the root cause of an incident.
    
    This structured model ensures the agent investigates systematically,
    not through free-text guessing.
    """
    id: str = Field(description="Unique identifier for the hypothesis")
    name: str = Field(description="Short name for the hypothesis")
    description: str = Field(description="What this hypothesis proposes")
    
    # What we need to test this
    evidence_needed: list[str] = Field(
        description="List of evidence items needed to test this hypothesis"
    )
    tools_to_use: list[str] = Field(
        description="List of tools/API calls to gather evidence"
    )
    
    # Current state
    status: HypothesisStatus = Field(
        default=HypothesisStatus.PENDING,
        description="Current status of the hypothesis"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence level (0-1) that this is the root cause"
    )
    
    # Evidence collected
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Evidence collected while testing this hypothesis"
    )
    
    # Conclusion
    conclusion: Optional[str] = Field(
        default=None,
        description="Final conclusion after testing"
    )


# Pre-defined hypothesis templates for the demo scenario
HYPOTHESIS_TEMPLATES = [
    {
        "id": "h1_task_failed",
        "name": "Pipeline task failed",
        "description": "A pipeline task failed with non-zero exit code",
        "evidence_needed": [
            "Check Tracer for failed tasks",
            "Get task exit codes and error messages",
        ],
        "tools_to_use": ["get_tracer_run", "get_tracer_tasks"],
    },
    {
        "id": "h2_output_missing",
        "name": "Output files missing",
        "description": "Expected output files were not created by the pipeline",
        "evidence_needed": [
            "Check files created during pipeline run",
            "Verify expected outputs exist",
        ],
        "tools_to_use": ["get_tracer_files"],
    },
    {
        "id": "h3_resource_exhaustion",
        "name": "Resource exhaustion",
        "description": "Pipeline failed due to running out of memory or CPU",
        "evidence_needed": [
            "Check pipeline resource usage",
            "Look for OOM or resource-related errors",
        ],
        "tools_to_use": ["get_tracer_run", "get_tracer_tasks"],
    },
]


def create_hypothesis_from_template(template: dict) -> Hypothesis:
    """Create a Hypothesis instance from a template."""
    return Hypothesis(
        id=template["id"],
        name=template["name"],
        description=template["description"],
        evidence_needed=template["evidence_needed"],
        tools_to_use=template["tools_to_use"],
    )

