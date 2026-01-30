"""Investigate node - planning and execution combined.

This node plans and executes evidence gathering.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable
from pydantic import BaseModel, Field

from app.agent.nodes.investigate.data_sources import detect_available_sources
from app.agent.nodes.investigate.execute_actions import execute_actions
from app.agent.nodes.investigate.post_process import (
    build_evidence_summary,
    merge_evidence,
    track_hypothesis,
)
from app.agent.nodes.investigate.prompt import build_investigation_prompt
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm
from app.agent.tools.tool_actions.investigation_actions import (
    get_available_actions,
    get_prioritized_actions,
)


class InvestigationPlan(BaseModel):
    """Structured plan for investigation."""

    actions: list[str] = Field(
        description="List of action names to execute (e.g., 'get_failed_jobs', 'get_error_logs')"
    )
    rationale: str = Field(description="Rationale for the chosen actions")


def _extract_keywords_from_problem(state: InvestigationState) -> list[str]:
    """Extract relevant keywords from the problem statement for action prioritization."""
    problem_md = state.get("problem_md", "")
    alert_name = state.get("alert_name", "")

    # Keywords that indicate specific investigation needs
    keyword_patterns = [
        "memory", "oom", "killed", "timeout", "slow", "hang",
        "failure", "failed", "error", "exception", "crash",
        "batch", "job", "task", "tool", "pipeline",
        "log", "logs", "trace", "debug",
        "metrics", "cpu", "disk", "resource",
    ]

    text = f"{problem_md} {alert_name}".lower()
    return [kw for kw in keyword_patterns if kw in text]


@traceable(name="node_investigate")
def node_investigate(state: InvestigationState) -> dict:
    """
    Combined investigate node:
    1) Detects available data sources from alert and state
    2) Filters actions by availability
    3) Uses LLM to decide which actions to execute
    4) Executes the selected actions with dynamic parameter extraction
    5) Merges and returns evidence
    """
    tracker = get_tracker()
    tracker.start("investigate", "Planning evidence gathering")

    # 1. Detect available data sources
    available_sources = detect_available_sources(state)
    debug_print(f"Available sources: {list(available_sources.keys())}")

    # 2. Filter actions by availability
    all_actions = get_available_actions()
    available_actions = [
        action
        for action in all_actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    # 3. Prioritize by keywords (secondary to availability)
    keywords = _extract_keywords_from_problem(state)
    if keywords:
        available_actions = get_prioritized_actions(keywords=keywords)
        # Re-filter by availability after prioritization
        available_actions = [
            action
            for action in available_actions
            if action.availability_check is None or action.availability_check(available_sources)
        ]

    # 4. Build prompt with available sources and filtered actions
    prompt = build_investigation_prompt(state, available_actions, available_sources)

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

    # 5. Generate plan via LLM
    llm = get_llm()
    structured_llm = llm.with_structured_output(InvestigationPlan)

    plan = structured_llm.with_config(
        run_name="LLM – Plan evidence gathering"
    ).invoke(prompt)
    print(f"[DEBUG] LLM Plan: {plan.actions}")
    print(f"[DEBUG] Rationale: {plan.rationale[:200]}")
    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")

    # 6. Execution phase with dynamic parameter extraction
    execution_results = execute_actions(plan.actions, available_sources)

    # 7. Post-processing phase
    evidence = merge_evidence(state, execution_results)
    executed_hypotheses = track_hypothesis(state, plan.actions, plan.rationale)
    evidence_summary = build_evidence_summary(execution_results)

    tracker.complete(
        "investigate",
        fields_updated=["evidence", "executed_hypotheses"],
        message=evidence_summary,
    )

    print(f"[DEBUG] Evidence being returned: {list(evidence.keys())}")
    print(f"[DEBUG] CloudWatch logs in evidence: {bool(evidence.get('cloudwatch_logs'))}")

    return {
        "evidence": evidence,
        "executed_hypotheses": executed_hypotheses,
    }
