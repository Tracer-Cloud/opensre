"""Hypothesis investigation - gather evidence to prove/disprove hypotheses."""

from src.agent.context.context_building import build_investigation_context
from src.agent.nodes.hypothesis_investigation.evidence_gathering import gather_evidence_for_context
from src.agent.nodes.publish_findings.render import (
    render_evidence,
    render_step_header,
)
from src.agent.state import InvestigationState


def main(state: InvestigationState) -> dict:
    """
    Main entry point for hypothesis investigation.

    Flow:
    1) Get context (already built in frame_problem or from state)
    2) Gather evidence (runtime data that proves/disproves hypotheses)
    3) Merge and return evidence
    """
    # Get context from state or build it
    context = state.get("context", {})
    if not context:
        context = build_investigation_context(state)

    render_step_header(1, "Gather runtime evidence")
    runtime_evidence = gather_evidence_for_context(context)

    # Merge evidence
    evidence = context.copy()
    tracer_web_run = evidence.get("tracer_web_run", {})
    if tracer_web_run.get("found") and runtime_evidence:
        evidence["tracer_web_run"] = {**tracer_web_run, **runtime_evidence}

    pipeline_run = evidence.get("pipeline_run", {})
    if pipeline_run.get("found") and runtime_evidence:
        evidence["pipeline_run"] = {**pipeline_run, **runtime_evidence}

    evidence.setdefault("s3", {"found": False, "error": "S3 storage check is not implemented"})
    evidence.setdefault("batch_jobs", {"found": False})

    render_evidence(evidence)
    return {"evidence": evidence}


def node_hypothesis_investigation(state: InvestigationState) -> dict:
    """LangGraph node wrapper."""
    return main(state)
