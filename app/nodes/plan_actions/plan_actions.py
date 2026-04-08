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

# Default tool budget if not specified in state
DEFAULT_TOOL_BUDGET = 10


def detect_reroute_trigger(
    evidence: dict[str, Any],
    available_sources: dict[str, dict],
    executed_hypotheses: list[dict[str, Any]],
) -> tuple[bool, str]:
    """
    Detect if new evidence requires rerouting to different tools.

    Rerouting is triggered when new evidence changes the likely source family,
    such as discovering an audit_key from S3 metadata that enables tracing
    external vendor interactions.

    Args:
        evidence: Current evidence gathered
        available_sources: Currently available data sources
        executed_hypotheses: History of executed hypotheses

    Returns:
        Tuple of (should_reroute, reroute_reason)
    """
    # Check if s3_audit source was discovered from evidence but not yet utilized
    s3_audit_in_sources = "s3_audit" in available_sources

    # Check if we've already done audit tracing in a previous loop
    s3_audit_already_executed = any(
        "get_s3_object" in (hyp.get("actions", [])) for hyp in executed_hypotheses
    )

    # Trigger reroute if s3_audit source available but audit not yet executed
    if s3_audit_in_sources and not s3_audit_already_executed:
        return (
            True,
            "s3_audit source discovered from S3 metadata - rerouting to external API tracing",
        )

    # Check for Grafana service name discovery without log fetching
    grafana_service_names = evidence.get("grafana_service_names", [])
    grafana_logs = evidence.get("grafana_logs", [])
    if grafana_service_names and not grafana_logs:
        grafana_logs_already_queried = any(
            "query_grafana_logs" in (hyp.get("actions", [])) for hyp in executed_hypotheses
        )
        if not grafana_logs_already_queried:
            return True, "grafana service names discovered but logs not yet fetched"

    # Check for vendor audit discovered in Lambda logs
    vendor_audit = evidence.get("vendor_audit_from_logs", {})
    vendor_audit_already_rerouted = any(
        hyp.get("audit", {}).get("reroute_reason")
        == "external vendor audit discovered in Lambda logs"
        for hyp in executed_hypotheses
    )
    if vendor_audit and not vendor_audit_already_rerouted:
        return True, "external vendor audit discovered in Lambda logs"

    return False, ""


def plan_actions(
    input_data,
    plan_model: type[BaseModel],
    _pipeline_name: str = "",
    resolved_integrations: dict[str, Any] | None = None,
) -> tuple[Any | None, dict[str, dict], list[str], list, bool, str]:
    """
    Interpret inputs, select actions, and request a plan from the LLM.

    Supports rerouting when new evidence changes the likely source family,
    and enforces per-step tool budgets to cap prompt size and execution breadth.

    Args:
        input_data: InvestigateInput (or compatible) object
        plan_model: Pydantic model for structured LLM output
        _pipeline_name: Unused (was for memory lookup, kept for caller compatibility)
        resolved_integrations: Pre-fetched integration credentials from resolve_integrations node

    Returns:
        Tuple of (plan_or_none, available_sources, available_action_names, available_actions, rerouted, reroute_reason)
    """
    # Get tool budget from input (with default)
    tool_budget = getattr(input_data, "tool_budget", DEFAULT_TOOL_BUDGET)

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

    # Detect if rerouting is needed based on new evidence
    rerouted, reroute_reason = detect_reroute_trigger(
        evidence=input_data.evidence,
        available_sources=available_sources,
        executed_hypotheses=input_data.executed_hypotheses,
    )
    if rerouted:
        debug_print(f"REROUTE TRIGGERED: {reroute_reason}")

    debug_print(f"Relevant sources: {list(available_sources.keys())}")

    all_actions = get_available_actions()
    keywords = extract_keywords(input_data.problem_md, input_data.alert_name)
    candidate_actions = get_prioritized_actions(keywords=keywords) if keywords else all_actions

    # Apply tool budget to cap the selected tool set before prompt construction
    available_actions, available_action_names = select_actions(
        actions=candidate_actions,
        available_sources=available_sources,
        executed_hypotheses=input_data.executed_hypotheses,
        tool_budget=tool_budget,
    )

    if not available_action_names:
        return (
            None,
            available_sources,
            available_action_names,
            available_actions,
            rerouted,
            reroute_reason,
        )

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
    # Insert at position 0 to ensure it is kept after budget truncation
    if (
        "s3_audit" in available_sources
        and "get_s3_object" not in plan.actions
        and "get_s3_object" in available_action_names
    ):
        plan.actions.insert(0, "get_s3_object")

    debug_print(f"Plan: {plan.actions} | {plan.rationale[:100]}...")
    if len(plan.actions) > tool_budget:
        debug_print(f"WARNING: Plan exceeds tool budget ({len(plan.actions)} > {tool_budget})")
        plan.actions = plan.actions[:tool_budget]

    return (
        plan,
        available_sources,
        available_action_names,
        available_actions,
        rerouted,
        reroute_reason,
    )
