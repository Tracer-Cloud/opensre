"""Agent state definition - supports both chat and investigation modes."""

import time
from typing import Any, Literal, TypedDict, cast

EvidenceSource = Literal["storage", "batch", "tracer_web", "cloudwatch", "aws_sdk", "knowledge", "grafana"]
AgentMode = Literal["chat", "investigation"]


class ChatMessage(TypedDict, total=False):
    role: Literal["system", "user", "assistant"]
    content: str
    tool_calls: list[dict[str, Any]]


class AgentState(TypedDict, total=False):
    """Unified state for chat and investigation modes.

    Chat mode: Uses messages for conversation with tools
    Investigation mode: Uses alert info for automated RCA
    """

    # Mode selection
    mode: AgentMode
    route: str  # "tracer_data" or "general" for chat routing

    # Auth context (from JWT)
    org_id: str
    user_id: str
    user_email: str
    user_name: str
    organization_slug: str

    # Chat mode - conversation
    messages: list[ChatMessage]

    # Investigation mode - alert input
    alert_name: str
    pipeline_name: str
    severity: str
    raw_alert: str | dict[str, Any]
    alert_json: dict[str, Any]

    # Investigation planning
    plan_sources: list[EvidenceSource]
    planned_actions: list[str]
    plan_rationale: str
    available_sources: dict[str, dict]
    available_action_names: list[str]

    # Shared context/evidence
    context: dict[str, Any]
    evidence: dict[str, Any]

    # Investigation analysis
    root_cause: str
    confidence: float
    validated_claims: list[dict[str, Any]]  # List of validated claims with evidence
    non_validated_claims: list[dict[str, Any]]  # List of non-validated claims
    validity_score: float  # Percentage of validated vs total claims
    investigation_recommendations: list[str]  # Recommended AWS SDK investigations if confidence low
    remediation_steps: list[str]  # Recommended remediation / prevention steps
    investigation_loop_count: int  # Number of times we've looped back to investigate
    hypotheses: list[str]  # Hypotheses to consider during diagnosis
    executed_hypotheses: list[
        dict[str, Any]
    ]  # History of executed hypotheses/API calls to avoid duplicates
    investigation_started_at: float  # Monotonic start time for timing calculations

    # Outputs
    slack_message: str
    problem_md: str


# Alias for backward compatibility
InvestigationState = AgentState

STATE_DEFAULTS: dict[str, Any] = {
    "mode": "chat",
    "route": "",
    "org_id": "",
    "user_id": "",
    "user_email": "",
    "user_name": "",
    "organization_slug": "",
    "messages": [],
    "plan_sources": [],
    "planned_actions": [],
    "plan_rationale": "",
    "available_sources": {},
    "available_action_names": [],
    "context": {},
    "evidence": {},
    "root_cause": "",
    "confidence": 0.0,
    "validated_claims": [],
    "non_validated_claims": [],
    "validity_score": 0.0,
    "investigation_recommendations": [],
    "remediation_steps": [],
    "investigation_loop_count": 0,
    "hypotheses": [],
    "executed_hypotheses": [],
    "slack_message": "",
    "problem_md": "",
}


def make_initial_state(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: str | dict[str, Any] | None = None,
) -> AgentState:
    """Create initial state for investigation mode."""
    state = cast(AgentState, {
        "mode": "investigation",
        "alert_name": alert_name,
        "pipeline_name": pipeline_name,
        "severity": severity,
        "investigation_started_at": time.monotonic(),
        **{k: v for k, v in STATE_DEFAULTS.items() if k not in ("mode", "messages")},
    })
    if raw_alert is not None:
        state["raw_alert"] = raw_alert
    return state


def make_chat_state(
    org_id: str = "",
    user_id: str = "",
    user_email: str = "",
    user_name: str = "",
    organization_slug: str = "",
    messages: list[ChatMessage] | None = None,
) -> AgentState:
    """Create initial state for chat mode."""
    return cast(AgentState, {
        "mode": "chat",
        "org_id": org_id,
        "user_id": user_id,
        "user_email": user_email,
        "user_name": user_name,
        "organization_slug": organization_slug,
        "messages": messages or [],
        "context": {},
    })
