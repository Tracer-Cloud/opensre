"""Plan actions node - planning only."""

from typing import cast

from langsmith import traceable
from pydantic import BaseModel, Field

from app.nodes.investigate.models import InvestigateInput
from app.nodes.investigate.types import PlanAudit
from app.nodes.plan_actions.plan_actions import plan_actions as build_plan_actions
from app.output import debug_print, get_tracker
from app.state import InvestigationState


class InvestigationPlan(BaseModel):
    """Structured plan for investigation."""

    actions: list[str] = Field(
        description="List of action names to execute (e.g., 'get_failed_jobs', 'get_error_logs')"
    )
    rationale: str = Field(description="Rationale for the chosen actions")


@traceable(name="node_plan_actions")
def node_plan_actions(state: InvestigationState) -> dict:
    """Plan investigation actions and write plan outputs to state.

    Supports rerouting when new evidence changes the likely source family,
    and enforces per-step tool budgets.
    """
    input_data = InvestigateInput.from_state(state)
    loop_count = state.get("investigation_loop_count", 0)

    tracker = get_tracker()
    tracker.start("plan_actions", "Planning evidence gathering")

    (
        plan,
        available_sources,
        available_action_names,
        _available_actions,
        rerouted,
        reroute_reason,
    ) = build_plan_actions(
        input_data=input_data,
        plan_model=InvestigationPlan,
        resolved_integrations=state.get("resolved_integrations"),
    )
    typed_plan = cast(InvestigationPlan | None, plan)

    planned_actions = typed_plan.actions if typed_plan else []
    plan_rationale = typed_plan.rationale if typed_plan else ""

    # Code-level guard: If the LLM returns an empty plan (e.g. for a healthy/informational alert),
    # forcibly seed a verification action to prevent infinite looping on insufficient evidence.
    if not planned_actions and available_action_names:
        fallback_candidates = [
            "query_grafana_metrics",
            "query_grafana_logs",
            "query_datadog_all",
            "query_datadog_logs",
            "query_honeycomb_traces",
            "query_coralogix_logs",
            "get_cloudwatch_logs",
            "get_host_metrics",
            "list_eks_pods",
            "get_eks_events",
        ]
        for candidate in fallback_candidates:
            if candidate in available_action_names:
                planned_actions = [candidate]
                plan_rationale = "Controller fallback: LLM returned empty plan. Forcing verification action."
                break
        if not planned_actions:
            planned_actions = [available_action_names[0]]
            plan_rationale = "Controller fallback: LLM returned empty plan. Forcing available verification action."

    # Build audit entry for this planning step
    audit_entry: PlanAudit = {
        "loop": loop_count,
        "tool_budget": input_data.tool_budget,
        "planned_count": len(planned_actions),
        "rerouted": rerouted,
    }
    if rerouted:
        audit_entry["reroute_reason"] = reroute_reason

    # Safety check: if we're in a loop but can't plan new actions, stop the investigation
    if not available_action_names or typed_plan is None:
        if loop_count > 0:
            debug_print(
                f"WARNING: Loop {loop_count} but no new actions can be planned. "
                "Clearing recommendations to stop loop."
            )
            # Clear recommendations to stop the routing from looping again
            tracker.complete(
                "plan_actions",
                fields_updated=[
                    "planned_actions",
                    "plan_rationale",
                    "available_sources",
                    "available_action_names",
                    "investigation_recommendations",
                ],
                message="No new actions planned (stopping loop)",
            )
            return {
                "planned_actions": [],
                "plan_rationale": "",
                "available_sources": available_sources,
                "available_action_names": available_action_names,
                "investigation_recommendations": [],  # Clear to stop loop
                "plan_audit": audit_entry,
            }

        debug_print("No new actions selected in planning.")
        tracker.complete(
            "plan_actions",
            fields_updated=[
                "planned_actions",
                "plan_rationale",
                "available_sources",
                "available_action_names",
            ],
            message="No new actions planned",
        )
        return {
            "planned_actions": [],
            "plan_rationale": "",
            "available_sources": available_sources,
            "available_action_names": available_action_names,
            "plan_audit": audit_entry,
        }

    tracker.complete(
        "plan_actions",
        fields_updated=[
            "planned_actions",
            "plan_rationale",
            "available_sources",
            "available_action_names",
        ],
        message=f"Planned actions: {planned_actions}",
    )

    return {
        "planned_actions": planned_actions,
        "plan_rationale": plan_rationale,
        "available_sources": available_sources,
        "available_action_names": available_action_names,
        "plan_audit": audit_entry,
    }
