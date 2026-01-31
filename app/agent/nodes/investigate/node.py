"""Investigate node - execute planned actions and post-process evidence."""

from langsmith import traceable

from app.agent.nodes.investigate.execution import execute_actions
from app.agent.nodes.investigate.models import InvestigateInput, InvestigateOutput
from app.agent.nodes.investigate.processing import (
    summarize_execution_results,
)
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.investigation_actions import get_available_actions


@traceable(name="node_investigate")
def node_investigate(state: InvestigationState) -> dict:
    """
    Execute node:
    1) Reads planned actions and sources from state
    2) Executes actions and post-processes evidence
    """
    # Extract only needed attributes from state
    input_data = InvestigateInput.from_state(state)

    tracker = get_tracker()
    tracker.start("investigate", "Executing planned actions")

    planned_actions = state.get("planned_actions", [])
    plan_rationale = state.get("plan_rationale", "")
    available_sources = state.get("available_sources", {})
    available_action_names = state.get("available_action_names", [])

    if not available_action_names or not planned_actions:
        debug_print("No planned actions to execute. Using existing evidence.")
        tracker.complete("investigate", fields_updated=["evidence"], message="No new actions")
        return {"evidence": input_data.evidence}

    all_actions = get_available_actions()
    actions_by_name = {action.name: action for action in all_actions}
    available_actions = {
        name: actions_by_name[name] for name in available_action_names if name in actions_by_name
    }

    # Execute actions and summarize results
    execution_results = execute_actions(planned_actions, available_actions, available_sources)
    evidence, executed_hypotheses, evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        action_names=planned_actions,
        current_evidence=input_data.evidence,
        executed_hypotheses=input_data.executed_hypotheses,
        investigation_loop_count=input_data.investigation_loop_count,
        rationale=plan_rationale,
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
