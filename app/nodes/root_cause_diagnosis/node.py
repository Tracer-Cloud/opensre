"""Root cause diagnosis node - orchestration and entry point."""

import os

from langsmith import traceable

from app.output import debug_print, get_tracker
from app.services import get_llm_for_reasoning, parse_root_cause
from app.state import InvestigationState

from .claim_validator import calculate_validity_score, validate_and_categorize_claims
from .evidence_checker import (
    check_evidence_availability,
    check_vendor_evidence_missing,
    is_clearly_healthy,
)
from .prompt_builder import build_diagnosis_prompt


def _short_circuit_enabled() -> bool:
    """Return True when the healthy short-circuit is active (default: on)."""
    return os.getenv("HEALTHY_SHORT_CIRCUIT", "true").lower() == "true"


def diagnose_root_cause(state: InvestigationState) -> dict:
    """
    Analyze evidence and determine root cause with integrated validation.

    Flow:
    1) Check if evidence is available
    2) Build prompt from evidence
    3) Call LLM to get root cause
    4) Validate claims against evidence
    5) Calculate validity score
    6) Generate recommendations if needed

    Args:
        state: Investigation state

    Returns:
        Dictionary with root_cause, validated_claims, validity_score, etc.
    """
    tracker = get_tracker()
    tracker.start("diagnose_root_cause", "Analyzing evidence")

    context = state.get("context", {})
    evidence = state.get("evidence", {})
    raw_alert = state.get("raw_alert", {})

    has_tracer, has_cloudwatch, has_alert = check_evidence_availability(context, evidence, raw_alert)

    if _short_circuit_enabled() and is_clearly_healthy(raw_alert, evidence):
        debug_print("Short-circuit: alert is clearly healthy, skipping LLM")
        return _handle_healthy_finding(state, tracker, evidence)

    if not has_tracer and not has_cloudwatch and not has_alert:
        return _handle_insufficient_evidence(state, tracker)

    prompt = build_diagnosis_prompt(state, evidence, "")

    debug_print("Invoking LLM for root cause analysis...")
    llm = get_llm_for_reasoning()
    response = llm.with_config(run_name="LLM – Analyze evidence and propose root cause").invoke(
        prompt
    )
    response_content = response.content if hasattr(response, "content") else str(response)
    response_text = response_content if isinstance(response_content, str) else str(response_content)

    result = parse_root_cause(response_text)

    validated_claims_list, non_validated_claims_list = validate_and_categorize_claims(
        result.validated_claims,
        result.non_validated_claims,
        evidence,
    )

    validity_score = calculate_validity_score(validated_claims_list, non_validated_claims_list)

    loop_count = state.get("investigation_loop_count", 0)

    recommendations: list[str] = []
    if check_vendor_evidence_missing(evidence) and loop_count < 3:
        recommendations.append("Fetch audit payload from S3 to trace external vendor interactions")
    next_loop_count = loop_count + 1 if recommendations else loop_count

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause", "validated_claims", "validity_score"],
        message=f"validity:{validity_score:.0%}",
    )

    return {
        "root_cause": result.root_cause,
        "root_cause_category": result.root_cause_category,
        "causal_chain": result.causal_chain,
        "validated_claims": validated_claims_list,
        "non_validated_claims": non_validated_claims_list,
        "validity_score": validity_score,
        "investigation_recommendations": recommendations,
        "remediation_steps": [],
        "investigation_loop_count": next_loop_count,
    }


def _handle_healthy_finding(
    state: InvestigationState, tracker, evidence: dict
) -> dict:
    """Return a deterministic healthy finding, bypassing the LLM.

    Called when is_clearly_healthy() confirms the alert is informational and all
    evidence keys are within normal operating bounds. Records "healthy_short_circuit"
    in the tracker so it appears in LangSmith traces.
    """
    alert_name = state.get("alert_name", "Health check")
    loop_count = state.get("investigation_loop_count", 0)

    validated_claims = [
        {"claim": f"{k} data confirmed within normal operating bounds", "validation_status": "validated"}
        for k in evidence
        if evidence[k]
    ]

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause", "root_cause_category"],
        message="healthy_short_circuit=true",
    )

    return {
        "root_cause": f"{alert_name}: All monitored metrics are within normal bounds. No failure detected.",
        "root_cause_category": "healthy",
        "causal_chain": [
            "Health check alert fired as a scheduled verification.",
            "All telemetry signals are stable and within normal operating ranges.",
            "No root cause exists.",
        ],
        "validated_claims": validated_claims,
        "non_validated_claims": [],
        "validity_score": 1.0,
        "investigation_recommendations": [],
        "remediation_steps": [],
        "investigation_loop_count": loop_count,
    }


def _handle_insufficient_evidence(state: InvestigationState, tracker) -> dict:
    """Handle case when no evidence is available."""
    debug_print("Warning: Limited evidence available")

    loop_count = state.get("investigation_loop_count", 0)
    evidence = state.get("evidence", {})

    alert_name = state.get("alert_name", "Unknown alert")
    pipeline_name = state.get("pipeline_name", "Unknown pipeline")
    severity = state.get("severity", "unknown")

    # If Grafana service names were just discovered but logs haven't been fetched yet,
    # loop back so node_plan_actions can query logs with the correct service name.
    recommendations: list[str] = []
    if evidence.get("grafana_service_names") and not evidence.get("grafana_logs") and loop_count < 3:
        recommendations.append("Query Grafana logs using discovered service names")

    next_loop_count = loop_count + 1

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause"],
        message="Insufficient evidence" + (f" — retrying ({next_loop_count})" if recommendations else ""),
    )

    return {
        "root_cause": f"{alert_name} on {pipeline_name} (severity: {severity}). Unable to determine exact root cause - insufficient evidence gathered.",
        "root_cause_category": "unknown",
        "validated_claims": [],
        "non_validated_claims": [
            {
                "claim": "Insufficient evidence available to validate root cause",
                "validation_status": "not_validated",
            }
        ],
        "validity_score": 0.0,
        "investigation_recommendations": recommendations,
        "remediation_steps": [],
        "investigation_loop_count": next_loop_count,
    }


@traceable(name="node_diagnose_root_cause")
def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return diagnose_root_cause(state)
