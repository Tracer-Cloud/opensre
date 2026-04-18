"""AgentState TypedDict and its Pydantic validator model.

WARNING — drift risk: AgentState (TypedDict) and AgentStateModel (Pydantic) must
stay in sync.  Whenever you add or remove a field in one, do the same in the other.
The test in tests/app/test_agent_state_sync.py asserts that both definitions share
the same set of keys and will fail if they diverge.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph import add_messages
from pydantic import ConfigDict, Field

from app.state.types import AgentMode, ChatMessageModel
from app.strict_config import StrictConfigModel


class AgentState(TypedDict, total=False):
    """Unified state for chat and investigation modes.

    Chat mode: Uses messages for conversation with tools
    Investigation mode: Uses alert info for automated RCA
    """

    # Mode selection
    mode: AgentMode
    route: str

    # Auth context (from JWT)
    org_id: str
    user_id: str
    user_email: str
    user_name: str
    organization_slug: str

    # Chat mode — conversation (add_messages reducer appends instead of replacing)
    messages: Annotated[list, add_messages]

    # Alert classification
    is_noise: bool

    # Investigation mode — alert input
    alert_name: str
    pipeline_name: str
    severity: str
    alert_source: str
    raw_alert: str | dict[str, Any]
    alert_json: dict[str, Any]

    # Investigation planning
    planned_actions: list[str]
    plan_rationale: str
    available_sources: dict[str, dict]
    available_action_names: list[str]

    # Tool budget enforcement - caps the number of tools per investigation step
    tool_budget: int  # Maximum tools to select per step (default: 10)

    # Audit trail for each planning step - records rerouting and budget decisions
    plan_audit: dict[str, Any]  # Audit data with loop, budget, reroute_reason, etc

    # Resolved integrations (from resolve_integrations node)
    resolved_integrations: dict[str, Any]

    # Shared context/evidence
    context: dict[str, Any]
    evidence: dict[str, Any]

    # Investigation analysis
    root_cause: str
    root_cause_category: str
    validated_claims: list[dict[str, Any]]
    non_validated_claims: list[dict[str, Any]]
    validity_score: float
    investigation_recommendations: list[str]
    remediation_steps: list[str]
    investigation_loop_count: int
    hypotheses: list[str]
    executed_hypotheses: list[dict[str, Any]]
    investigation_started_at: float

    # Placeholder→original map for reversible infrastructure identifier masking
    masking_map: dict[str, str]

    # Slack context (when triggered from Slack message)
    slack_context: dict[str, Any]

    # Discord context (when triggered from Discord interaction)
    discord_context: dict[str, Any]

    # LangGraph context (injected from config by inject_auth_node)
    thread_id: str
    run_id: str
    _auth_token: str

    # Outputs
    slack_message: str
    problem_md: str
    summary: str
    problem_report: dict[str, Any]
    report: str


InvestigationState = AgentState


class AgentStateModel(StrictConfigModel):
    """Runtime-validated state envelope used by state constructors."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=(), populate_by_name=True)

    mode: AgentMode = "chat"
    route: str = ""
    org_id: str = ""
    user_id: str = ""
    user_email: str = ""
    user_name: str = ""
    organization_slug: str = ""
    messages: list[ChatMessageModel] = Field(default_factory=list)
    is_noise: bool = False
    alert_name: str = ""
    pipeline_name: str = ""
    severity: str = ""
    alert_source: str = ""
    raw_alert: str | dict[str, Any] = Field(default_factory=dict)
    alert_json: dict[str, Any] = Field(default_factory=dict)
    planned_actions: list[str] = Field(default_factory=list)
    plan_rationale: str = ""
    available_sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    available_action_names: list[str] = Field(default_factory=list)
    tool_budget: int = Field(
        default=10, ge=1, le=50, description="Maximum tools to select per step"
    )
    plan_audit: dict[str, Any] = Field(
        default_factory=dict, description="Audit trail for planning step"
    )
    resolved_integrations: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    root_cause: str = ""
    root_cause_category: str = ""
    validated_claims: list[dict[str, Any]] = Field(default_factory=list)
    non_validated_claims: list[dict[str, Any]] = Field(default_factory=list)
    validity_score: float = 0.0
    investigation_recommendations: list[str] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)
    investigation_loop_count: int = 0
    hypotheses: list[str] = Field(default_factory=list)
    executed_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    investigation_started_at: float = 0.0
    masking_map: dict[str, str] = Field(default_factory=dict)
    slack_context: dict[str, Any] = Field(default_factory=dict)
    discord_context: dict[str, Any] = Field(default_factory=dict)
    thread_id: str = ""
    run_id: str = ""
    auth_token: str = Field(default="", alias="_auth_token", exclude=True)
    slack_message: str = ""
    problem_md: str = ""
    summary: str = ""
    problem_report: dict[str, Any] = Field(default_factory=dict)
    report: str = ""
