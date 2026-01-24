"""Investigate node - planning and execution combined."""

import time

from langsmith import traceable
from pydantic import BaseModel, Field

from src.agent.nodes.publish_findings.render import (
    console,
    render_evidence,
    render_plan,
    render_step_header,
)
from src.agent.state import EvidenceSource, InvestigationState
from src.agent.tools.llm import get_llm
from src.agent.tools.tool_actions import (
    get_error_logs,
    get_failed_jobs,
    get_failed_tools,
    get_host_metrics,
)
from src.agent.utils import get_executed_sources


class InvestigationPlan(BaseModel):
    """Structured plan for investigation."""

    sources: list[EvidenceSource] = Field(description="List of evidence sources to check")
    rationale: str = Field(description="Rationale for the chosen sources")


def _get_available_sources() -> list[EvidenceSource]:
    """Get list of evidence sources that are actually available."""
    return ["batch", "tracer_web", "cloudwatch"]


@traceable(name="node_investigate")
def node_investigate(state: InvestigationState) -> dict:
    """
    Combined investigate node:
    1) Uses LLM to decide which tools to run based on context
    2) Immediately executes the selected tools
    3) Merges and returns evidence
    """
    start_time = time.time()
    render_step_header(1, "Investigate")

    # 1. Planning phase
    executed_sources_set = get_executed_sources(state)
    available_sources = _get_available_sources()
    available_sources_filtered = [s for s in available_sources if s not in executed_sources_set]

    if not available_sources_filtered:
        console.print("  [yellow]⚠️  All sources already executed. Using existing evidence.[/]")
        return {"evidence": state.get("evidence", {})}

    # Generate plan via LLM
    llm = get_llm()
    structured_llm = llm.with_structured_output(InvestigationPlan)

    prompt = f"""You are investigating a data pipeline incident.
Problem Context:
{state.get('problem_md', 'No problem statement available')}

Available Sources: {', '.join(available_sources_filtered)}
Executed Sources: {', '.join(executed_sources_set)}

Recommendations from previous analysis:
{chr(10).join(f"- {r}" for s in state.get('investigation_recommendations', []) for r in s)}

Task: Select the most relevant sources to investigate now.
"""

    plan = structured_llm.invoke(prompt)
    render_plan(plan.sources, rationale=plan.rationale)

    # 2. Execution phase
    evidence = state.get("evidence", {}).copy()
    context = evidence  # In our new flow, context is already in evidence

    # Get trace_id from tracer_web_run context (built in frame_problem)
    tracer_web_run = context.get("tracer_web_run", {})
    trace_id = tracer_web_run.get("trace_id")

    if not trace_id:
        console.print("  [red]❌ ERROR: No trace_id found in context. Cannot gather evidence.[/]")
        return {"evidence": evidence}

    runtime_evidence = {}

    # Call tools based on selected sources
    if "tracer_web" in plan.sources or "batch" in plan.sources:
        try:
            failed_jobs_data = get_failed_jobs(trace_id)
            if isinstance(failed_jobs_data, dict) and "error" not in failed_jobs_data:
                runtime_evidence["failed_jobs"] = failed_jobs_data.get("failed_jobs", [])
                runtime_evidence["total_jobs"] = failed_jobs_data.get("total_jobs", 0)
        except Exception:
            pass

        try:
            failed_tools_data = get_failed_tools(trace_id)
            if isinstance(failed_tools_data, dict) and "error" not in failed_tools_data:
                runtime_evidence["failed_tools"] = failed_tools_data.get("failed_tools", [])
                runtime_evidence["total_tools"] = failed_tools_data.get("total_tools", 0)
        except Exception:
            pass

    if "tracer_web" in plan.sources:
        try:
            error_logs_data = get_error_logs(trace_id, size=500, error_only=True)
            if isinstance(error_logs_data, dict) and "error" not in error_logs_data:
                runtime_evidence["error_logs"] = error_logs_data.get("logs", [])
                runtime_evidence["total_logs"] = error_logs_data.get("total_logs", 0)
        except Exception:
            pass

    if "cloudwatch" in plan.sources:
        try:
            host_metrics_data = get_host_metrics(trace_id)
            if isinstance(host_metrics_data, dict) and "error" not in host_metrics_data:
                runtime_evidence["host_metrics"] = host_metrics_data.get("metrics", {})
        except Exception:
            pass

    # Merge evidence
    if tracer_web_run.get("found"):
        evidence["tracer_web_run"] = {**tracer_web_run, **runtime_evidence}
    else:
        evidence.update(runtime_evidence)

    # Track this hypothesis
    executed_hypotheses = state.get("executed_hypotheses", [])
    new_hypothesis = {
        "sources": plan.sources,
        "rationale": plan.rationale,
        "loop_count": state.get("investigation_loop_count", 0),
    }
    executed_hypotheses.append(new_hypothesis)

    render_evidence(evidence)

    latency_ms = int((time.time() - start_time) * 1000)
    console.print(f"  [dim]Investigate Node Latency: {latency_ms}ms[/]")

    return {
        "evidence": evidence,
        "executed_hypotheses": executed_hypotheses,
    }
