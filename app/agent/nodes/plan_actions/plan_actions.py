"""Plan investigation actions from available inputs."""

from typing import Any

from pydantic import BaseModel

from app.agent.nodes.plan_actions.build_prompt import (
    plan_actions_with_llm,
    select_actions,
)
from app.agent.nodes.plan_actions.detect_sources import detect_sources
from app.agent.nodes.plan_actions.extract_keywords import extract_keywords
from app.agent.output import debug_print
from app.agent.tools.clients import get_llm
from app.agent.tools.tool_actions.investigation_registry import (
    get_available_actions,
    get_prioritized_actions,
)


def plan_actions(
    input_data,
    plan_model: type[BaseModel],
    pipeline_name: str = "",
    resolved_integrations: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, dict], list[str], list]:
    """
    Interpret inputs, select actions, and request a plan from the LLM.

    Args:
        input_data: InvestigateInput (or compatible) object
        plan_model: Pydantic model for structured LLM output
        pipeline_name: Pipeline name from state (for memory lookup)
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
            # Add audit payload as discoverable S3 source
            available_sources["s3_audit"] = {"bucket": bucket, "key": audit_key}
            print(f"[DEBUG] Added s3_audit source: s3://{bucket}/{audit_key}")

    print(f"[DEBUG] Available sources: {list(available_sources.keys())}")
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

    # Load memory context if enabled
    from app.agent.memory import get_memory_context, is_memory_enabled
    from app.agent.memory.architecture_discovery import find_architecture_doc_for_pipeline

    memory_context = ""
    if is_memory_enabled() and pipeline_name:
        seed_paths = find_architecture_doc_for_pipeline(pipeline_name)
        memory_context = get_memory_context(pipeline_name=pipeline_name, seed_paths=seed_paths)
        if memory_context:
            debug_print("[MEMORY] Loaded context for action planning")

    # Use fast model (Haiku) if memory provides guidance
    use_fast = bool(memory_context)
    llm = get_llm(use_fast_model=use_fast)

    plan = plan_actions_with_llm(
        llm=llm,
        plan_model=plan_model,
        problem_md=input_data.problem_md,
        executed_hypotheses=input_data.executed_hypotheses,
        available_actions=available_actions,
        available_sources=available_sources,
        memory_context=memory_context,
    )
    print(f"[DEBUG] LLM Plan: {plan.actions}")
    print(f"[DEBUG] Rationale: {plan.rationale[:200]}")
    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")

    return plan, available_sources, available_action_names, available_actions
