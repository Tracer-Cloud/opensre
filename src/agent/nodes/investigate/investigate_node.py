"""Investigate node - planning and execution combined.

This node plans and executes evidence gathering.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable
from pydantic import BaseModel, Field

from src.agent.nodes.investigate.execution import execute_actions
from src.agent.nodes.investigate.post_process import (
    build_evidence_summary,
    merge_evidence,
    track_hypothesis,
)
from src.agent.nodes.investigate.prompt import build_investigation_prompt
from src.agent.output import debug_print, get_tracker
from src.agent.state import InvestigationState
from src.agent.tools.clients import get_llm
from src.agent.tools.tool_actions.investigation_actions import get_available_actions


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
    1) Uses LLM to decide which actions to execute based on context
    2) Immediately executes the selected actions
    3) Merges and returns evidence
    """
    tracker = get_tracker()
    tracker.start("investigate", "Planning evidence gathering")

    # 1. Planning phase - build prompt with rich action metadata
    available_actions = get_available_actions()
    prompt = build_investigation_prompt(state, available_actions)

    # Check if we have any actions available
    executed_actions_flat = set()
    for hyp in state.get("executed_hypotheses", []):
        actions = hyp.get("actions", [])
        if isinstance(actions, list):
            executed_actions_flat.update(actions)
    available_action_names = [
        action.name for action in available_actions if action.name not in executed_actions_flat
    ]

    if not available_action_names:
        debug_print("All actions already executed. Using existing evidence.")
        tracker.complete("investigate", fields_updated=["evidence"], message="No new actions")
        return {"evidence": state.get("evidence", {})}

    # Generate plan via LLM
    llm = get_llm()
    structured_llm = llm.with_structured_output(InvestigationPlan)
    plan = structured_llm.invoke(prompt)
    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")

    # 2. Execution phase
    execution_results = execute_actions(state, plan.actions)

    # 3. Post-processing phase
    evidence = merge_evidence(state, execution_results)
    executed_hypotheses = track_hypothesis(state, plan.actions, plan.rationale)
    evidence_summary = build_evidence_summary(execution_results)

    tracker.complete(
        "investigate",
        fields_updated=["evidence", "executed_hypotheses"],
        message=evidence_summary,
    )

    return {
        "evidence": evidence,
        "executed_hypotheses": executed_hypotheses,
    }
