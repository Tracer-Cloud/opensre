"""
LangGraph nodes.

Nodes orchestrate tools, LLM, and rendering. They only return state patches.
"""

# Domain layer
from src.agent.domain.state import InvestigationState
from src.agent.domain.tools import check_s3_marker, check_nextflow_finalize
from src.agent.domain.prompts import (
    s3_interpretation_prompt,
    nextflow_interpretation_prompt,
    root_cause_synthesis_prompt,
)

# Infrastructure layer
from src.agent.infrastructure.llm import stream_completion, parse_bullets, parse_root_cause

# Presentation layer
from src.agent.presentation.render import (
    render_step_header,
    render_api_response,
    render_llm_thinking,
    render_dot,
    render_newline,
    render_bullets,
    render_root_cause_complete,
    render_generating_outputs,
)
from src.agent.presentation.report import format_slack_message, format_problem_md, ReportContext


# ─────────────────────────────────────────────────────────────────────────────
# Node: Check S3
# ─────────────────────────────────────────────────────────────────────────────

def node_check_s3(state: InvestigationState) -> dict:
    """Check S3 and interpret results with LLM."""
    render_step_header(1, "Checking S3 for data artifacts...")
    
    # Tool call
    result = check_s3_marker("tracer-processed-data", "events/2026-01-13/")
    render_api_response("S3", f"marker_exists={result.marker_exists}, files={result.file_count}")
    
    # LLM interpretation
    render_llm_thinking()
    prompt = s3_interpretation_prompt(result)
    response = stream_completion(prompt, on_chunk=lambda _: render_dot())
    render_newline()
    
    # Parse and display
    interpretation = parse_bullets(response)
    render_bullets(interpretation.bullets)
    
    return {
        "s3_marker_exists": result.marker_exists,
        "s3_file_count": result.file_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: Check Nextflow
# ─────────────────────────────────────────────────────────────────────────────

def node_check_nextflow(state: InvestigationState) -> dict:
    """Check Nextflow and interpret results with LLM."""
    render_step_header(2, "Checking Nextflow pipeline status...")
    
    # Tool call
    result = check_nextflow_finalize("events-etl")
    render_api_response("Nextflow", f"status={result.status}, error={result.error or 'none'}")
    
    # LLM interpretation
    render_llm_thinking()
    prompt = nextflow_interpretation_prompt(result)
    response = stream_completion(prompt, on_chunk=lambda _: render_dot())
    render_newline()
    
    # Parse and display
    interpretation = parse_bullets(response)
    render_bullets(interpretation.bullets)
    
    return {
        "nextflow_finalize_status": result.status,
        "nextflow_logs": result.logs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: Determine Root Cause
# ─────────────────────────────────────────────────────────────────────────────

def node_determine_root_cause(state: InvestigationState) -> dict:
    """Synthesize all evidence into root cause conclusion."""
    render_step_header(3, "Synthesizing root cause analysis...")
    
    # LLM synthesis
    render_llm_thinking()
    prompt = root_cause_synthesis_prompt(
        alert_name=state["alert_name"],
        affected_table=state["affected_table"],
        s3_marker_exists=state["s3_marker_exists"],
        s3_file_count=state["s3_file_count"],
        nextflow_status=state["nextflow_finalize_status"],
        nextflow_logs=state["nextflow_logs"],
    )
    response = stream_completion(prompt, on_chunk=lambda _: render_dot())
    render_newline()
    
    # Parse and display
    result = parse_root_cause(response)
    bullets = [line.strip() for line in result.root_cause.split('\n') if line.strip()]
    render_root_cause_complete(bullets, result.confidence)
    
    return {
        "root_cause": result.root_cause,
        "confidence": result.confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: Generate Outputs
# ─────────────────────────────────────────────────────────────────────────────

def node_output(state: InvestigationState) -> dict:
    """Generate Slack message and problem.md."""
    render_generating_outputs()
    
    ctx: ReportContext = {
        "affected_table": state["affected_table"],
        "root_cause": state["root_cause"],
        "confidence": state["confidence"],
        "s3_marker_exists": state["s3_marker_exists"],
        "nextflow_finalize_status": state["nextflow_finalize_status"],
    }
    
    return {
        "slack_message": format_slack_message(ctx),
        "problem_md": format_problem_md(ctx),
    }

