"""Root cause diagnosis node - orchestration and entry point."""

from langsmith import traceable

from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm, parse_root_cause

from .claim_validator import calculate_validity_score, validate_and_categorize_claims
from .evidence_checker import check_evidence_availability
from .prompt_builder import build_diagnosis_prompt


def diagnose_root_cause(state: InvestigationState) -> dict:
    """
    Analyze evidence and determine root cause with integrated validation.

    Flow:
    1) Check if evidence is available
    2) Build prompt from evidence
    3) Call LLM to get root cause
    4) Validate claims against evidence
    5) Calculate confidence and validity
    6) Generate recommendations if needed

    Args:
        state: Investigation state

    Returns:
        Dictionary with root_cause, confidence, validated_claims, etc.
    """
    tracker = get_tracker()
    tracker.start("diagnose_root_cause", "Analyzing evidence")

    context = state.get("context", {})
    evidence = state.get("evidence", {})
    raw_alert = state.get("raw_alert", {})

    # Check evidence availability
    has_tracer, has_cloudwatch, has_alert = check_evidence_availability(context, evidence, raw_alert)

    # If no evidence at all, return low-confidence result
    if not has_tracer and not has_cloudwatch and not has_alert:
        return _handle_insufficient_evidence(state, tracker)

    # Load memory context if enabled
    memory_context = _load_memory_context(state)

    # Build prompt from evidence
    prompt = build_diagnosis_prompt(state, evidence, memory_context)

    # Call LLM for root cause analysis
    debug_print("Invoking LLM for root cause analysis...")
    use_fast = bool(memory_context)
    llm = get_llm(use_fast_model=use_fast)
    response = llm.with_config(run_name="LLM – Analyze evidence and propose root cause").invoke(
        prompt
    )
    response_content = response.content if hasattr(response, "content") else str(response)
    response_text = response_content if isinstance(response_content, str) else str(response_content)

    # Parse response
    result = parse_root_cause(response_text)

    # Validate claims against evidence
    validated_claims_list, non_validated_claims_list = validate_and_categorize_claims(
        result.validated_claims,
        result.non_validated_claims,
        evidence,
    )

    # Calculate validity score
    validity_score = calculate_validity_score(validated_claims_list, non_validated_claims_list)

    # Update confidence based on validity
    final_confidence = (result.confidence * 0.4) + (validity_score * 0.6)

    loop_count = state.get("investigation_loop_count", 0)

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause", "confidence", "validated_claims", "validity_score"],
        message=f"confidence:{final_confidence:.0%}, validity:{validity_score:.0%}",
    )

    return {
        "root_cause": result.root_cause,
        "confidence": final_confidence,
        "validated_claims": validated_claims_list,
        "non_validated_claims": non_validated_claims_list,
        "validity_score": validity_score,
        "investigation_recommendations": [],
        "remediation_steps": [],
        "investigation_loop_count": loop_count,
    }


def _handle_insufficient_evidence(state: InvestigationState, tracker) -> dict:
    """Handle case when no evidence is available."""
    debug_print("Warning: Limited evidence available - proceeding with low confidence")

    loop_count = state.get("investigation_loop_count", 0) + 1

    problem = state.get("problem_md", "Pipeline failure detected")

    tracker.complete(
        "diagnose_root_cause",
        fields_updated=["root_cause", "confidence"],
        message="Insufficient evidence",
    )

    return {
        "root_cause": f"{problem}. Limited evidence available for analysis - unable to determine exact root cause without additional diagnostic data.",
        "confidence": 0.2,
        "validated_claims": [],
        "non_validated_claims": [
            {
                "claim": "Insufficient evidence available to validate root cause",
                "validation_status": "not_validated",
            }
        ],
        "validity_score": 0.0,
        "investigation_recommendations": [],
        "remediation_steps": [],
        "investigation_loop_count": loop_count,
    }


def _load_memory_context(state: InvestigationState) -> str:
    """Load memory context if enabled."""
    from app.agent.memory import get_memory_context

    pipeline_name = state.get("pipeline_name", "")
    memory_context = get_memory_context(pipeline_name=pipeline_name)

    if memory_context:
        debug_print("[MEMORY] Loaded context for diagnosis")

    return memory_context


@traceable(name="node_diagnose_root_cause")
def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return diagnose_root_cause(state)
