"""Investigate node - execute planned actions and post-process evidence."""

import logging
from typing import cast

from langsmith import traceable

from app.nodes.investigate.execution import execute_actions
from app.nodes.investigate.models import InvestigateInput, InvestigateOutput
from app.nodes.investigate.processing import (
    summarize_execution_results,
)
from app.nodes.investigate.types import PlanAudit
from app.output import debug_print, get_tracker
from app.state import InvestigationState
from app.tools.investigation_registry import get_available_actions

logger = logging.getLogger(__name__)


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
    available_sources = cast(dict[str, dict[str, object]], state.get("available_sources", {}))
    available_action_names = state.get("available_action_names", [])

    if not available_action_names or not planned_actions:
        debug_print("No planned actions to execute. Using existing evidence.")
        tracker.complete("investigate", fields_updated=["evidence"], message="No new actions")
        return {"evidence": input_data.evidence}

    all_actions = get_available_actions()
    actions_by_name = {action.name: action for action in all_actions}

    # Build available actions dictionary from ALL planned actions (not just available_action_names)
    # This allows retrying actions that may have been filtered out by select_actions
    available_actions = {}
    for name in planned_actions:
        if name in actions_by_name:
            available_actions[name] = actions_by_name[name]
        else:
            logger.warning("Planned action '%s' not found in action registry", name)

    # Execute actions and summarize results
    execution_results = execute_actions(planned_actions, available_actions, available_sources)
    raw_plan_audit = state.get("plan_audit")
    plan_audit = cast(PlanAudit | None, raw_plan_audit if isinstance(raw_plan_audit, dict) else None)
    evidence, executed_hypotheses, evidence_summary = summarize_execution_results(
        execution_results=execution_results,
        current_evidence=input_data.evidence,
        executed_hypotheses=input_data.executed_hypotheses,
        investigation_loop_count=input_data.investigation_loop_count,
        rationale=plan_rationale,
        plan_audit=plan_audit,
    )

    # If we just discovered Grafana service names and the current service_name is still
    # the raw pipeline name (no logs found yet), update it so the next loop queries logs
    # with the real service name that exists in Loki.
    grafana_source = available_sources.get("grafana")
    discovered_services_raw = evidence.get("grafana_service_names", [])
    discovered_services = (
        [str(service) for service in discovered_services_raw]
        if isinstance(discovered_services_raw, list)
        else []
    )
    if discovered_services and grafana_source:
        current_service = str(grafana_source.get("service_name", ""))
        pipeline_name = str(grafana_source.get("pipeline_name", ""))
        no_logs_yet = not evidence.get("grafana_logs")
        # Only update if the current service_name doesn't match anything in Loki
        if no_logs_yet and current_service not in discovered_services:
            # Prefer a service that contains the pipeline name, otherwise take the first
            best = next(
                (s for s in discovered_services if pipeline_name and pipeline_name in s),
                discovered_services[0],
            )
            available_sources["grafana"]["service_name"] = best

    tracker.complete(
        "investigate",
        fields_updated=["evidence", "executed_hypotheses"],
        message=evidence_summary,
    )

    output = InvestigateOutput(evidence=evidence, executed_hypotheses=executed_hypotheses)
    return {**output.to_dict(), "available_sources": available_sources}
