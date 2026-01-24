"""Simplified root cause diagnosis with integrated validation."""

import time

from langsmith import traceable

from src.agent.nodes.publish_findings.render import (
    console,
    render_analysis,
    render_step_header,
)
from src.agent.state import InvestigationState
from src.agent.tools.llm import get_llm, parse_root_cause


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
    start_time = time.time()
    render_step_header(1, "Root cause diagnosis")

    evidence = state.get("evidence", {})
    web_run = evidence.get("tracer_web_run", {})

    # Check if we have evidence
    if not web_run.get("found"):
        console.print("\n[bold red]❌ ERROR: No evidence available for analysis[/]")
        console.print("[yellow]No tracer_web_run data found in evidence.[/]")
        return {
            "root_cause": "No evidence available for analysis",
            "confidence": 0.0,
            "validated_claims": [],
            "non_validated_claims": [],
            "validity_score": 0.0,
        }

    # Build simple prompt from evidence
    prompt = _build_simple_prompt(state, web_run)

    # Call LLM
    render_step_header(2, "LLM inference")
    llm = get_llm()
    response = llm.invoke(prompt)
    response_text = response.content if hasattr(response, "content") else str(response)

    # Parse response
    result = parse_root_cause(response_text)

    # Simple validation: check if claims reference available evidence
    validated_claims_list = []
    non_validated_claims_list = []

    for claim in result.validated_claims:
        is_valid = _simple_validate_claim(claim, web_run)
        validated_claims_list.append(
            {
                "claim": claim,
                "evidence_sources": _extract_evidence_sources(claim, web_run),
                "validation_status": "validated" if is_valid else "failed_validation",
            }
        )

    for claim in result.non_validated_claims:
        is_valid = _simple_validate_claim(claim, web_run)
        if is_valid:
            validated_claims_list.append(
                {
                    "claim": claim,
                    "evidence_sources": _extract_evidence_sources(claim, web_run),
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

    # Render results
    render_analysis(result.root_cause, final_confidence)
    console.print(f"  [dim]Validity: {validity_score:.0%} ({len(validated_claims_list)}/{total_claims} claims validated)[/]")

    # Generate recommendations if confidence is low
    investigation_recommendations = []
    loop_count = state.get("investigation_loop_count", 0)
    if final_confidence < 0.6 or validity_score < 0.5:
        investigation_recommendations = _generate_simple_recommendations(
            non_validated_claims_list, evidence
        )
        if investigation_recommendations:
            loop_count += 1
            console.print(f"  [cyan]→ Returning to hypothesis generation (loop {loop_count}/5)[/]")

    latency_ms = int((time.time() - start_time) * 1000)
    console.print(f"  [dim]Node latency: {latency_ms}ms[/]")

    return {
        "root_cause": result.root_cause,
        "confidence": final_confidence,
        "validated_claims": validated_claims_list,
        "non_validated_claims": non_validated_claims_list,
        "validity_score": validity_score,
        "investigation_recommendations": investigation_recommendations,
        "investigation_loop_count": loop_count,
    }


def _build_simple_prompt(state: InvestigationState, web_run: dict) -> str:
    """Build a simple, focused prompt from evidence."""
    problem = state.get("problem_md", "")
    hypotheses = state.get("hypotheses", [])

    # Extract key evidence
    failed_jobs = web_run.get("failed_jobs", [])
    failed_tools = web_run.get("failed_tools", [])
    error_logs = web_run.get("error_logs", [])[:10]  # Limit to 10 most recent
    host_metrics = web_run.get("host_metrics", {})

    prompt = f"""Analyze the following incident and determine the root cause.

PROBLEM:
{problem}

HYPOTHESES TO INVESTIGATE:
{chr(10).join(f"- {h}" for h in hypotheses[:5])}

EVIDENCE:
"""
    if failed_jobs:
        prompt += f"\nFailed Jobs ({len(failed_jobs)}):\n"
        for job in failed_jobs[:5]:
            prompt += f"- {job.get('job_name', 'Unknown')}: {job.get('status_reason', 'No reason')}\n"

    if failed_tools:
        prompt += f"\nFailed Tools ({len(failed_tools)}):\n"
        for tool in failed_tools[:5]:
            prompt += f"- {tool.get('tool_name', 'Unknown')}: exit_code={tool.get('exit_code')}\n"

    if error_logs:
        prompt += f"\nError Logs ({len(error_logs)}):\n"
        for log in error_logs[:5]:
            prompt += f"- {log.get('message', '')[:200]}\n"

    if host_metrics and host_metrics.get("data"):
        prompt += "\nHost Metrics: Available (CPU, memory, disk)\n"

    prompt += """
Based on this evidence, provide:
ROOT_CAUSE: <clear explanation of the root cause>
VALIDATED_CLAIMS:
- <claim directly supported by evidence>
- <another validated claim>
NON_VALIDATED_CLAIMS:
- <claim inferred but not directly supported>
CONFIDENCE: <0-100%>
"""

    return prompt


def _simple_validate_claim(claim: str, web_run: dict) -> bool:
    """Simple validation: check if claim references available evidence."""
    claim_lower = claim.lower()

    # Check logs
    if ("log" in claim_lower or "error" in claim_lower) and web_run.get("total_logs", 0) == 0:
        return False

    # Check metrics
    if ("memory" in claim_lower or "cpu" in claim_lower) and not web_run.get("host_metrics", {}).get("data"):
        return False

    # Check jobs
    return not (("job" in claim_lower or "batch" in claim_lower) and len(web_run.get("failed_jobs", [])) == 0)


def _extract_evidence_sources(claim: str, web_run: dict) -> list[str]:
    """Extract evidence sources mentioned in a claim."""
    sources = []
    claim_lower = claim.lower()

    if ("log" in claim_lower or "error" in claim_lower) and web_run.get("total_logs", 0) > 0:
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and web_run.get("failed_jobs"):
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and web_run.get("failed_tools"):
        sources.append("tracer_tools")
    if ("metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower) and web_run.get("host_metrics", {}).get("data"):
        sources.append("host_metrics")

    return sources if sources else ["evidence_analysis"]


def _generate_simple_recommendations(
    non_validated_claims: list[dict], evidence: dict
) -> list[str]:
    """Generate simple investigation recommendations."""
    if not non_validated_claims:
        return []

    recommendations = []
    web_run = evidence.get("tracer_web_run", {})

    # Check what's missing
    if not web_run.get("host_metrics", {}).get("data"):
        recommendations.append("Query CloudWatch Metrics for CPU and memory usage")
    if web_run.get("total_logs", 0) == 0:
        recommendations.append("Fetch CloudWatch Logs for detailed error messages")
    if not web_run.get("failed_jobs"):
        recommendations.append("Query AWS Batch job details using describe_jobs API")

    return recommendations[:5]


@traceable(name="node_diagnose_root_cause")
def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper with LangSmith tracking."""
    return main(state)
