"""
Investigation state definition.

The single source of truth for state shape across the graph.
Linear deterministic flow: plan -> gather_evidence -> analyze -> output.
"""

from typing import Any, Literal, TypedDict

# ─────────────────────────────────────────────────────────────────────────────
# Evidence Source Types
# ─────────────────────────────────────────────────────────────────────────────
EvidenceSource = Literal["storage", "batch", "tracer_web", "cloudwatch"]


# ─────────────────────────────────────────────────────────────────────────────
# State Definition
# ─────────────────────────────────────────────────────────────────────────────
class InvestigationState(TypedDict, total=False):
    """
    State passed through the investigation graph.

    Linear flow:
    1. Input: Alert information that triggers the investigation
    2. Planning: Deterministic rules produce plan_sources
    3. Evidence: Direct tool calls, results stored as structured data
    4. Analysis: Root cause and confidence from LLM
    5. Output: Formatted reports for Slack/Markdown
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Input - from alert
    # ─────────────────────────────────────────────────────────────────────────
    alert_name: str
    affected_table: str
    severity: str
    raw_alert: str | dict[str, Any]
    alert_json: dict[str, Any]

    # ─────────────────────────────────────────────────────────────────────────
    # Planning - deterministic plan based on alert type
    # ─────────────────────────────────────────────────────────────────────────
    plan_sources: list[EvidenceSource]

    # ─────────────────────────────────────────────────────────────────────────
    # Evidence - structured data from tool calls (not messages)
    # ─────────────────────────────────────────────────────────────────────────
    evidence: dict[str, Any]

    # ─────────────────────────────────────────────────────────────────────────
    # Analysis - from LLM synthesis
    # ─────────────────────────────────────────────────────────────────────────
    root_cause: str
    confidence: float
    validated_claims: list[dict[str, Any]]  # List of validated claims with evidence
    non_validated_claims: list[dict[str, Any]]  # List of non-validated claims
    validity_score: float  # Percentage of validated vs total claims
    investigation_recommendations: list[str]  # Recommended AWS SDK investigations if confidence low
    investigation_loop_count: int  # Number of times we've looped back to investigate
    executed_hypotheses: list[
        dict[str, Any]
    ]  # History of executed hypotheses/API calls to avoid duplicates

    # ─────────────────────────────────────────────────────────────────────────
    # Outputs - formatted reports
    # ─────────────────────────────────────────────────────────────────────────
    slack_message: str
    problem_md: str


# ─────────────────────────────────────────────────────────────────────────────
# State Initialization
# ─────────────────────────────────────────────────────────────────────────────
# Required keys and their defaults defined in one place
STATE_DEFAULTS: dict[str, Any] = {
    "plan_sources": [],
    "evidence": {},
    "root_cause": "",
    "confidence": 0.0,
    "validated_claims": [],
    "non_validated_claims": [],
    "validity_score": 0.0,
    "investigation_recommendations": [],
    "investigation_loop_count": 0,
    "executed_hypotheses": [],
    "slack_message": "",
    "problem_md": "",
}


def make_initial_state(
    alert_name: str,
    affected_table: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
) -> InvestigationState:
    """
    Create the initial state for an investigation.

    All required keys and defaults are defined in STATE_DEFAULTS.
    Input fields (alert_name, affected_table, severity) are required.
    """
    state: InvestigationState = {
        # Input fields (required)
        "alert_name": alert_name,
        "affected_table": affected_table,
        "severity": severity,
        # Defaults for all other fields
        **STATE_DEFAULTS,
    }
    if raw_alert is not None:
        state["raw_alert"] = raw_alert
    return state
