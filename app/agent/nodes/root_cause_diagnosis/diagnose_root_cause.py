"""Simplified root cause diagnosis with integrated validation.

This node analyzes evidence and determines root cause.
It updates state fields but does NOT render output directly.
"""

from langsmith import traceable

from app.agent.output import debug_print, get_tracker
from app.agent.state import InvestigationState
from app.agent.tools.clients import get_llm, parse_root_cause


@traceable(name="diagnose_root_cause")
def main(state: InvestigationState) -> dict:
    """
    Simplified root cause diagnosis with integrated validation.

    Flow:
    1) Check if evidence is available
    2) Build simple prompt from evidence
    3) Call LLM to get root cause
    4) Validate claims against evidence
    5) Calculate confidence and validity
    """
    tracker = get_tracker()
    tracker.start("diagnose_root_cause", "Analyzing evidence")

    context = state.get("context", {})
    evidence = state.get("evidence", {})
    web_run = context.get("tracer_web_run", {})

    # Check if we have context
    if not web_run.get("found"):
        tracker.error("diagnose_root_cause", "No evidence available for analysis")
        return {
            "root_cause": "No evidence available for analysis",
            "confidence": 0.0,
            "validated_claims": [],
            "non_validated_claims": [],
            "validity_score": 0.0,
        }

    # Build simple prompt from context and evidence
    prompt = _build_simple_prompt(state, evidence)

    # Call LLM
    debug_print("Invoking LLM for root cause analysis...")
    llm = get_llm()
    response = llm.with_config(
        run_name="LLM – Analyze evidence and propose root cause"
    ).invoke(prompt)
    response_text = response.content if hasattr(response, "content") else str(response)

    # Parse response
    result = parse_root_cause(response_text)

    # Simple validation: check if claims reference available evidence
    validated_claims_list = []
    non_validated_claims_list = []

    for claim in result.validated_claims:
        is_valid = _simple_validate_claim(claim, evidence)
        validated_claims_list.append(
            {
                "claim": claim,
                "evidence_sources": _extract_evidence_sources(claim, evidence),
                "validation_status": "validated" if is_valid else "failed_validation",
            }
        )

    for claim in result.non_validated_claims:
        is_valid = _simple_validate_claim(claim, evidence)
        if is_valid:
            validated_claims_list.append(
                {
                    "claim": claim,
                    "evidence_sources": _extract_evidence_sources(claim, evidence),
                    "validation_status": "validated",
                }
            )
        else:
            non_validated_claims_list.append(
                {
                    "claim": claim,
                    "validation_status": "not_validated",
                }
            )

    # Calculate validity score
    total_claims = len(validated_claims_list) + len(non_validated_claims_list)
    validity_score = len(validated_claims_list) / total_claims if total_claims > 0 else 0.0

    # Update confidence based on validity
    final_confidence = (result.confidence * 0.4) + (validity_score * 0.6)

    # Generate recommendations if confidence is low
    investigation_recommendations = []
    loop_count = state.get("investigation_loop_count", 0)
    if final_confidence < 0.6 or validity_score < 0.5:
        investigation_recommendations = _generate_simple_recommendations(
            non_validated_claims_list, evidence
        )
        if investigation_recommendations:
            loop_count += 1
            debug_print(f"Returning to hypothesis generation (loop {loop_count}/5)")

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
        "investigation_recommendations": investigation_recommendations,
        "investigation_loop_count": loop_count,
    }


def _build_simple_prompt(state: InvestigationState, evidence: dict) -> str:
    """Build an evidence-based prompt for root cause analysis."""
    problem = state.get("problem_md", "")
    hypotheses = state.get("hypotheses", [])

    # Allowed evidence sources the model can reference (keeps grounding consistent)
    allowed_sources = ["aws_batch_jobs", "tracer_tools", "logs", "host_metrics"]

    # Extract key investigation findings from evidence
    failed_jobs = evidence.get("failed_jobs", [])
    failed_tools = evidence.get("failed_tools", [])
    error_logs = evidence.get("error_logs", [])[:10]  # Limit to 10 most recent
    host_metrics = evidence.get("host_metrics", {})

    prompt = f"""You are an experienced SRE writing a short RCA (root cause analysis) for a data pipeline incident.

Goal: Be helpful and accurate. Prefer evidence-backed explanations over speculation.
If the exact root cause cannot be proven, provide the most likely explanation based on observed evidence,
and clearly state what is unknown.

DEFINITIONS:
- VALIDATED_CLAIMS: Directly supported by the evidence shown below (observed facts).
- NON_VALIDATED_CLAIMS: Plausible hypotheses or contributing factors that are NOT directly proven by the evidence.

RULES:
- Do NOT introduce external domain knowledge that is not visible in the evidence (e.g., what a tool usually does).
- VALIDATED_CLAIMS should be factual and specific (no "maybe", "likely", "appears").
- NON_VALIDATED_CLAIMS may include "likely/maybe", but must stay consistent with evidence.
- Keep each claim to one sentence.
- When possible, mention which evidence source supports a validated claim using one of:
  {", ".join(allowed_sources)}.

PROBLEM:
{problem}

HYPOTHESES TO CONSIDER (may be incomplete):
{chr(10).join(f"- {h}" for h in hypotheses[:5]) if hypotheses else "- None"}

EVIDENCE:
"""

    if failed_jobs:
        prompt += f"\nAWS Batch Failed Jobs ({len(failed_jobs)}):\n"
        for job in failed_jobs[:5]:
            prompt += f"- {job.get('job_name', 'Unknown')}: {job.get('status_reason', 'No reason')}\n"
    else:
        prompt += "\nAWS Batch Failed Jobs: None\n"

    if failed_tools:
        prompt += f"\nFailed Tools ({len(failed_tools)}):\n"
        for tool in failed_tools[:5]:
            prompt += f"- {tool.get('tool_name', 'Unknown')}: exit_code={tool.get('exit_code')}\n"
    else:
        prompt += "\nFailed Tools: None\n"

    if error_logs:
        prompt += f"\nError Logs ({len(error_logs)}):\n"
        for log in error_logs[:5]:
            prompt += f"- {log.get('message', '')[:200]}\n"
    else:
        prompt += "\nError Logs: None\n"

    if host_metrics and host_metrics.get("data"):
        prompt += "\nHost Metrics: Available (CPU, memory, disk)\n"
    else:
        prompt += "\nHost Metrics: None\n"

    prompt += f"""
OUTPUT FORMAT (follow exactly):

ROOT_CAUSE:
<1–2 sentences. If not proven, say "Most likely ..." and state what's missing. Do not say only "Unable to determine".>

VALIDATED_CLAIMS:
- <one factual claim> [evidence: <one of {", ".join(allowed_sources)}>]
- <another factual claim> [evidence: <one of {", ".join(allowed_sources)}>]

NON_VALIDATED_CLAIMS:
- <one plausible hypothesis consistent with evidence>
- <another plausible hypothesis>
(If you include hypotheses, focus on explaining the failure mechanism and what data is missing to confirm it.)

CONFIDENCE: <0-100 integer>
"""

    return prompt


def _simple_validate_claim(claim: str, evidence: dict) -> bool:
    """Simple validation: check if claim references available evidence."""
    claim_lower = claim.lower()

    # Check logs (from evidence)
    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("total_logs", 0) == 0:
        return False

    # Check metrics (from evidence)
    if ("memory" in claim_lower or "cpu" in claim_lower) and not evidence.get(
        "host_metrics", {}
    ).get("data"):
        return False

    # Check jobs (from evidence)
    return not (
        ("job" in claim_lower or "batch" in claim_lower)
        and len(evidence.get("failed_jobs", [])) == 0
    )


def _extract_evidence_sources(claim: str, evidence: dict) -> list[str]:
    """Extract evidence sources mentioned in a claim."""
    sources = []
    claim_lower = claim.lower()

    if ("log" in claim_lower or "error" in claim_lower) and evidence.get("total_logs", 0) > 0:
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and evidence.get("failed_jobs"):
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and evidence.get("failed_tools"):
        sources.append("tracer_tools")
    if ("metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower) and evidence.get(
        "host_metrics", {}
    ).get("data"):
        sources.append("host_metrics")

    return sources if sources else ["evidence_analysis"]


def _generate_simple_recommendations(
    non_validated_claims: list[dict], evidence: dict
) -> list[str]:
    """Generate simple investigation recommendations."""
    if not non_validated_claims:
        return []

    recommendations = []

    # Check what's missing (investigation findings from evidence)
    if not evidence.get("host_metrics", {}).get("data"):
        recommendations.append("Query CloudWatch Metrics for CPU and memory usage")
    if evidence.get("total_logs", 0) == 0:
        recommendations.append("Fetch CloudWatch Logs for detailed error messages")
    if not evidence.get("failed_jobs"):
        recommendations.append("Query AWS Batch job details using describe_jobs API")

    return recommendations[:5]


@traceable(name="node_diagnose_root_cause")
def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)
