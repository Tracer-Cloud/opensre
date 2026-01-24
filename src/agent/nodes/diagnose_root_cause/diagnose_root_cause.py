"""Diagnose root cause from collected evidence."""

from src.agent.nodes.diagnose_root_cause.prompt import build_diagnosis_prompt
from src.agent.nodes.publish_findings.render import render_analysis
from src.agent.state import InvestigationState
from src.agent.tools.llm import parse_root_cause, stream_completion


def node_diagnose_root_cause(state: InvestigationState) -> dict:
    """Synthesize evidence into root cause using LLM."""
    prompt = build_diagnosis_prompt(state, state.get("evidence", {}))
    result = parse_root_cause(stream_completion(prompt))
    render_analysis(result.root_cause, result.confidence)
    return {"root_cause": result.root_cause, "confidence": result.confidence}
