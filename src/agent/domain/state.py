"""
Investigation state definition.

The single source of truth for state shape across the graph.
"""

from typing import TypedDict


class InvestigationState(TypedDict, total=False):
    """State passed through the investigation graph."""
    
    # Input - from alert
    alert_name: str
    affected_table: str
    severity: str
    
    # Evidence - from tool calls
    s3_marker_exists: bool
    s3_file_count: int
    nextflow_finalize_status: str | None
    nextflow_logs: str | None
    
    # Analysis - from LLM
    root_cause: str
    confidence: float
    
    # Outputs - formatted reports
    slack_message: str
    problem_md: str

