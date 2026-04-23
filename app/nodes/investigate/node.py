"""Investigate node - execute planned actions and post-process evidence."""

import logging
from typing import Any, cast

from langsmith import traceable

from app.masking import MaskingContext
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


def _load_opensre_telemetry_into_evidence(
    state: InvestigationState,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Merge OpenRCA / HF CSV telemetry into evidence; return (evidence, resolved_integrations or None)."""
    raw_alert = state.get("raw_alert")
    prior = dict(state.get("evidence") or {})
    if not isinstance(raw_alert, dict):
        return prior, None
    try:
        from app.integrations.opensre.seed_evidence import merge_opensre_seed_into_state

        seed = merge_opensre_seed_into_state(
            raw_alert,
            state.get("resolved_integrations"),
            prior,
        )
    except Exception:
        logger.exception("OpenSRE telemetry load failed during evidence gathering")
        return prior, None
    ev = seed.get("evidence") or prior
    if ev.get("opensre_telemetry_seed"):
        debug_print(f"OpenSRE telemetry loaded from {ev.get('opensre_telemetry_dir', '')}")
    return ev, seed.get("resolved_integrations")


@traceable(name="node_investigate")
def node_investigate(state: InvestigationState) -> dict:
    """
    Execute node:
    1) Loads OpenSRE/OpenRCA CSV telemetry into evidence when applicable
    2) Executes planned actions and post-processes evidence
    """
    tracker = get_tracker()
    tracker.start("investigate", "Gathering evidence")

    base_evidence, resolved_from_opensre = _load_opensre_telemetry_into_evidence(state)
    input_data = InvestigateInput.from_state(state).model_copy(update={"evidence": base_evidence})

    planned_actions = state.get("planned_actions", [])
    plan_rationale = state.get("plan_rationale", "")
    available_sources = cast(dict[str, dict[str, object]], state.get("available_sources", {}))
    available_action_names = state.get("available_action_names", [])

    if not available_action_names or not planned_actions:
        debug_print("No planned actions to execute. Using existing evidence.")
        tracker.complete("investigate", fields_updated=["evidence"], message="No new actions")
        out: dict[str, Any] = {"evidence": input_data.evidence}
        if resolved_from_opensre is not None:
            out["resolved_integrations"] = resolved_from_opensre
        return out

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
    plan_audit = cast(
        PlanAudit | None, raw_plan_audit if isinstance(raw_plan_audit, dict) else None
    )
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
            matching_services = [
                s for s in discovered_services if pipeline_name and pipeline_name in s
            ]
            if matching_services:
                available_sources["grafana"]["service_name"] = matching_services[0]
            elif discovered_services:
                available_sources["grafana"]["service_name"] = discovered_services[0]

    # Apply reversible masking to evidence before it flows to downstream LLM
    # nodes. No-op when OPENSRE_MASK_ENABLED is not set.
    masking_ctx = MaskingContext.from_state(cast(dict[str, object], state))
    masked_evidence = masking_ctx.mask_value(evidence)
    masking_map = masking_ctx.to_state()

    tracker.complete(
        "investigate",
        fields_updated=["evidence", "executed_hypotheses"],
        message=evidence_summary,
    )

    output = InvestigateOutput(evidence=masked_evidence, executed_hypotheses=executed_hypotheses)
    result: dict[str, object] = {
        **output.to_dict(),
        "available_sources": available_sources,
    }
    if resolved_from_opensre is not None:
        result["resolved_integrations"] = resolved_from_opensre
    if masking_map:
        result["masking_map"] = masking_map
    return result
