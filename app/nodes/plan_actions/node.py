"""Plan actions node - planning only."""

from langsmith import traceable
from pydantic import BaseModel, Field

from app.nodes.investigate.models import InvestigateInput
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
    """Plan investigation actions and write plan outputs to state."""
    input_data = InvestigateInput.from_state(state)
    loop_count = state.get("investigation_loop_count", 0)

    tracker = get_tracker()
    tracker.start("plan_actions", "Planning evidence gathering")

    plan, available_sources, available_action_names, _available_actions = build_plan_actions(
        input_data=input_data,
        plan_model=InvestigationPlan,
        resolved_integrations=state.get("resolved_integrations"),
    )

    planned_actions = plan.actions if plan else []
    plan_rationale = plan.rationale if plan else ""

    # Safety check: if we're in a loop but can't plan new actions, stop the investigation
    if not available_action_names or plan is None:
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
    }
