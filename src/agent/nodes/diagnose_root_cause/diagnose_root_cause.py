"""Diagnose root cause from collected evidence."""

from src.agent.nodes.diagnose_root_cause.error_handling import check_evidence_sources
from src.agent.nodes.diagnose_root_cause.investigate import perform_deep_investigation
from src.agent.nodes.diagnose_root_cause.prompt import build_diagnosis_prompt
from src.agent.nodes.publish_findings.render import (
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
        console.print(f"  [yellow]Sources skipped:[/] {', '.join(investigation['evidence_sources_skipped'])}")

    prompt = build_diagnosis_prompt(state, state.get("evidence", {}), investigation)
    render_step_header(2, "Root cause inference")
    result = parse_root_cause(stream_completion(prompt))
    render_analysis(result.root_cause, result.confidence)

    return {
        "root_cause": result.root_cause,
        "confidence": result.confidence,
        "investigation": investigation,
    }


def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)
