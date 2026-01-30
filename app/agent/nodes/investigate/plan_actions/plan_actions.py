"""Plan investigation actions from available inputs."""

from typing import Any

from pydantic import BaseModel

from app.agent.nodes.investigate.plan_actions.interpretation_to_action_mapping import (
    interpret_inputs,
)
from app.agent.nodes.investigate.plan_actions.prompt import (
    plan_actions as get_plan_from_llm,
)
from app.agent.nodes.investigate.plan_actions.prompt import (
    select_actions,
)
from app.agent.output import debug_print
from app.agent.tools.clients import get_llm


def plan_actions(
    input_data,
    plan_model: type[BaseModel],
) -> tuple[Any | None, dict[str, dict], list[str], list]:
    """
    Interpret inputs, select actions, and request a plan from the LLM.

    Args:
        input_data: InvestigateInput (or compatible) object
        plan_model: Pydantic model for structured LLM output

    Returns:
        Tuple of (plan_or_none, available_sources, available_action_names, available_actions)
    """
    available_sources = interpret_inputs(input_data.raw_alert, input_data.context)
    debug_print(f"Relevant sources: {list(available_sources.keys())}")

    available_actions, available_action_names = select_actions(
        available_sources=available_sources,
        problem_md=input_data.problem_md,
        alert_name=input_data.alert_name,
        executed_hypotheses=input_data.executed_hypotheses,
    )

    if not available_action_names:
        return None, available_sources, available_action_names, available_actions

    llm = get_llm()
    plan = get_plan_from_llm(
        llm=llm,
        plan_model=plan_model,
        problem_md=input_data.problem_md,
        investigation_recommendations=input_data.investigation_recommendations,
        executed_hypotheses=input_data.executed_hypotheses,
        available_actions=available_actions,
        available_sources=available_sources,
    )
    print(f"[DEBUG] LLM Plan: {plan.actions}")
    print(f"[DEBUG] Rationale: {plan.rationale[:200]}")
    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")

    return plan, available_sources, available_action_names, available_actions
