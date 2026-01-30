"""Plan investigation actions from available inputs."""

from typing import Any

from pydantic import BaseModel

from app.agent.nodes.investigate.plan_actions.build_prompt import (
    plan_actions_with_llm,
    select_actions,
)
from app.agent.nodes.investigate.plan_actions.detect_sources import detect_sources
from app.agent.nodes.investigate.plan_actions.extract_keywords import extract_keywords
from app.agent.output import debug_print
from app.agent.tools.clients import get_llm
from app.agent.tools.tool_actions.investigation_actions import (
    get_available_actions,
    get_prioritized_actions,
)


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
    available_sources = detect_sources(input_data.raw_alert, input_data.context)
    debug_print(f"Relevant sources: {list(available_sources.keys())}")

    all_actions = get_available_actions()
    keywords = extract_keywords(input_data.problem_md, input_data.alert_name)
    candidate_actions = (
        get_prioritized_actions(keywords=keywords) if keywords else all_actions
    )

    available_actions, available_action_names = select_actions(
        actions=candidate_actions,
        available_sources=available_sources,
        executed_hypotheses=input_data.executed_hypotheses,
    )

    if not available_action_names:
        return None, available_sources, available_action_names, available_actions

    llm = get_llm()
    plan = plan_actions_with_llm(
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
