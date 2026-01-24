"""Diagnose root cause from collected evidence."""

from src.agent.nodes.diagnose_root_cause.error_handling import check_evidence_sources
from src.agent.nodes.diagnose_root_cause.investigate import perform_deep_investigation
from src.agent.nodes.diagnose_root_cause.prompt import build_diagnosis_prompt
from src.agent.nodes.rca_report_publishing.render import (
    console,
    render_analysis,
    render_step_header,
)
from src.agent.state import InvestigationState
from src.agent.tools.llm import parse_root_cause, stream_completion


def main(state: InvestigationState) -> dict:
    """
    Main entry point for root cause diagnosis.

    Flow:
    1) Perform deep investigation across all evidence sources
    2) Check if evidence is available
    3) Analyze and infer root cause using LLM
    """
    render_step_header(1, "Deep multi-source investigation")
    investigation = perform_deep_investigation(state)

    has_evidence, error_message = check_evidence_sources(investigation)
    if not has_evidence:
        return {
            "root_cause": error_message,
            "confidence": 0.0,
            "investigation": investigation,
        }

    # Show investigation summary
    evidence_sources_checked = investigation.get("evidence_sources_checked", [])
    console.print(f"  [dim]Evidence sources checked:[/] {len(evidence_sources_checked)}")
    console.print(f"  [dim]Tools executed:[/] {len(investigation.get('tools_executed', []))}")
    console.print(f"  [dim]Logs analyzed:[/] {investigation.get('logs_analyzed', 0)}")
    if investigation.get("evidence_sources_skipped"):
        console.print(
            f"  [yellow]Sources skipped:[/] {', '.join(investigation['evidence_sources_skipped'])}"
        )

    prompt = build_diagnosis_prompt(state, state.get("evidence", {}), investigation)
    render_step_header(2, "Root cause inference")
    result = parse_root_cause(stream_completion(prompt))

    # Render using new validated/non-validated format if available
    if result.validated_claims or result.non_validated_claims:
        from src.agent.nodes.rca_report_publishing.render import render_validated_claims

        # Calculate initial validity score (will be recalculated in validation node)
        total_claims = len(result.validated_claims) + len(result.non_validated_claims)
        initial_validity = len(result.validated_claims) / total_claims if total_claims > 0 else 0.0
        render_validated_claims(
            [{"claim": c, "evidence_sources": []} for c in result.validated_claims],
            [{"claim": c} for c in result.non_validated_claims],
            initial_validity,
            result.confidence,
        )
    else:
        render_analysis(result.root_cause, result.confidence)

    # Convert claims to structured format with evidence references
    validated_claims_list = [
        {"claim": claim, "evidence_sources": _extract_evidence_sources(claim, investigation)}
        for claim in result.validated_claims
    ]
    non_validated_claims_list = [
        {"claim": claim, "reason": "Inferred but not directly supported by evidence"}
        for claim in result.non_validated_claims
    ]

    return {
        "root_cause": result.root_cause,
        "confidence": result.confidence,
        "validated_claims": validated_claims_list,
        "non_validated_claims": non_validated_claims_list,
        "investigation": investigation,
    }


def _extract_evidence_sources(claim: str, investigation: dict) -> list[str]:
    """Extract evidence sources mentioned in a claim."""
    sources = []
    evidence_sources_checked = str(investigation.get("evidence_sources_checked", []))

    # Check if claim mentions specific evidence types
    claim_lower = claim.lower()
    if ("log" in claim_lower or "error" in claim_lower) and (
        "tracer_logs" in evidence_sources_checked or "opensearch" in evidence_sources_checked
    ):
        sources.append("logs")
    if ("job" in claim_lower or "batch" in claim_lower) and "aws_batch" in evidence_sources_checked:
        sources.append("aws_batch_jobs")
    if "tool" in claim_lower and "tracer_tools" in evidence_sources_checked:
        sources.append("tracer_tools")
    if (
        "metric" in claim_lower or "memory" in claim_lower or "cpu" in claim_lower
    ) and "host_metrics" in evidence_sources_checked:
        sources.append("host_metrics")

    return sources if sources else ["evidence_analysis"]


def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)
