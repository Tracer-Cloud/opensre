"""Plan investigation actions from available inputs."""

from typing import Any

from pydantic import BaseModel

from app.nodes.plan_actions.build_prompt import (
    plan_actions_with_llm,
    select_actions,
)
from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.plan_actions.extract_keywords import extract_keywords
from app.output import debug_print
from app.services import get_llm_for_tools
from app.tools.investigation_registry import (
    get_available_actions,
    get_prioritized_actions,
)


def plan_actions(
    input_data,
    plan_model: type[BaseModel],
    _pipeline_name: str = "",
    resolved_integrations: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, dict], list[str], list]:
    """
    Interpret inputs, select actions, and request a plan from the LLM.

    Args:
        input_data: InvestigateInput (or compatible) object
        plan_model: Pydantic model for structured LLM output
        _pipeline_name: Unused (was for memory lookup, kept for caller compatibility)
        resolved_integrations: Pre-fetched integration credentials from resolve_integrations node

    Returns:
        Tuple of (plan_or_none, available_sources, available_action_names, available_actions)
    """
    available_sources = detect_sources(
        input_data.raw_alert, input_data.context, resolved_integrations=resolved_integrations
    )

    # Enhance sources with dynamically discovered information from evidence (e.g., audit_key from S3 metadata)
    s3_object = input_data.evidence.get("s3_object", {})
    if s3_object.get("found") and s3_object.get("metadata", {}).get("audit_key"):
        audit_key = s3_object["metadata"]["audit_key"]
        bucket = s3_object.get("bucket")
        if bucket and "s3_audit" not in available_sources:
            available_sources["s3_audit"] = {"bucket": bucket, "key": audit_key}
            debug_print(f"Added s3_audit source: s3://{bucket}/{audit_key}")

    debug_print(f"Relevant sources: {list(available_sources.keys())}")

    all_actions = get_available_actions()
    keywords = extract_keywords(input_data.problem_md, input_data.alert_name)
    candidate_actions = get_prioritized_actions(keywords=keywords) if keywords else all_actions

    available_actions, available_action_names = select_actions(
        actions=candidate_actions,
        available_sources=available_sources,
        executed_hypotheses=input_data.executed_hypotheses,
    )

    if not available_action_names:
        return None, available_sources, available_action_names, available_actions

    llm = get_llm_for_tools()

    plan = plan_actions_with_llm(
        llm=llm,
        plan_model=plan_model,
        problem_md=input_data.problem_md,
        executed_hypotheses=input_data.executed_hypotheses,
        available_actions=available_actions,
        available_sources=available_sources,
        memory_context="",
    )

    # Ensure audit trail is fetched when s3_audit source is available
    if (
        "s3_audit" in available_sources
        and "get_s3_object" not in plan.actions
        and "get_s3_object" in available_action_names
    ):
        plan.actions.append("get_s3_object")

    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")

    return plan, available_sources, available_action_names, available_actions
