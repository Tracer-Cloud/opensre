"""Plan actions node - planning only."""

from langsmith import traceable
from pydantic import BaseModel, Field

from app.agent.nodes.investigate.models import InvestigateInput
from app.agent.nodes.investigate.plan_actions.plan_actions import (
    plan_actions as build_plan_actions,
)
from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState


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

    tracker = get_tracker()
    tracker.start("plan_actions", "Planning evidence gathering")

    plan, available_sources, available_action_names, _available_actions = build_plan_actions(
        input_data=input_data,
        plan_model=InvestigationPlan,
    )

    planned_actions = plan.actions if plan else []
    plan_rationale = plan.rationale if plan else ""

    if not available_action_names or plan is None:
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
