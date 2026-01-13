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
        "id": "h1_transform_failed",
        "name": "Nextflow transform failed",
        "description": "The Nextflow transformation step failed, preventing output file creation",
        "evidence_needed": [
            "Check if output file exists in S3",
            "Check Nextflow transform step status",
        ],
        "tools_to_use": ["list_s3_files", "get_step_status"],
    },
    {
        "id": "h2_loader_crashed",
        "name": "Service B loader crashed",
        "description": "The data loader (Service B) crashed and is not running",
        "evidence_needed": [
            "Check loader status",
            "Verify loader is running",
        ],
        "tools_to_use": ["get_loader_status"],
    },
    {
        "id": "h3_success_marker_missing",
        "name": "Output marker missing",
        "description": "The _SUCCESS marker was not written, blocking downstream ingestion",
        "evidence_needed": [
            "Check if _SUCCESS marker exists",
            "Check if output file exists",
            "Check Nextflow finalize step status",
        ],
        "tools_to_use": ["check_success_marker", "list_s3_files", "get_step_status"],
    },
    {
        "id": "h4_upstream_missing",
        "name": "Upstream data missing",
        "description": "The raw input data was never written by the upstream service",
        "evidence_needed": [
            "Check if raw input file exists in S3",
        ],
        "tools_to_use": ["list_s3_files"],
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

