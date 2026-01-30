"""Investigate node - planning and execution combined.

This node plans and executes evidence gathering.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable
from pydantic import BaseModel, Field

from app.agent.nodes.investigate.models import InvestigateInput, InvestigateOutput
from app.agent.nodes.investigate.execution import execute_actions
from app.agent.nodes.investigate.plan_actions import plan_actions
from app.agent.nodes.investigate.processing import (
    summarize_execution_results,
)
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState


class InvestigationPlan(BaseModel):
    """Structured plan for investigation."""

    actions: list[str] = Field(
        description="List of action names to execute (e.g., 'get_failed_jobs', 'get_error_logs')"
    )
    rationale: str = Field(description="Rationale for the chosen actions")


@traceable(name="node_investigate")
def node_investigate(state: InvestigationState) -> dict:
    """
    Combined investigate node:
    1) Interprets available data sources from alert and context
    2) Selects actions based on availability, keywords, and history
    3) Plans actions via LLM
    4) Executes actions and post-processes evidence
    """
    # Extract only needed attributes from state
    input_data = InvestigateInput.from_state(state)

    tracker = get_tracker()
    tracker.start("investigate", "Planning evidence gathering")

    # Plan required actions with LLM
    plan, available_sources, available_action_names, available_actions = plan_actions(
        input_data=input_data,
        plan_model=InvestigationPlan,
    )

    if not available_action_names or plan is None:
        debug_print("All actions already executed. Using existing evidence.")
        tracker.complete("investigate", fields_updated=["evidence"], message="No new actions")
        return {"evidence": input_data.evidence}

    # Execute actions and summarize results
    execution_results = execute_actions(
        plan.actions, available_actions, available_sources
    )
    evidence, executed_hypotheses, evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        action_names=plan.actions,
        current_evidence=input_data.evidence,
        executed_hypotheses=input_data.executed_hypotheses,
        investigation_loop_count=input_data.investigation_loop_count,
        rationale=plan.rationale,
    )

    tracker.complete(
        "investigate",
        fields_updated=["evidence", "executed_hypotheses"],
        message=evidence_summary,
    )

    print(f"[DEBUG] Evidence being returned: {list(evidence.keys())}")
    print(f"[DEBUG] CloudWatch logs in evidence: {bool(evidence.get('cloudwatch_logs'))}")

    output = InvestigateOutput(evidence=evidence, executed_hypotheses=executed_hypotheses)
    return output.to_dict()
