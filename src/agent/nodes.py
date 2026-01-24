"""
LangGraph nodes for the investigation workflow.

Architecture:
    - node_plan: Deterministic rules produce plan_sources based on alert type
    - node_gather_evidence: Execute plan by calling tools directly
    - node_analyze: Synthesize root cause from collected evidence
    - node_output: Generate formatted reports

Nodes are pure: inputs in, state patches out. No rendering.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

# Infrastructure layer
from src.agent.clients.llm import parse_root_cause, stream_completion

# Domain layer
from src.agent.domain.state import EvidenceSource, InvestigationState
from src.agent.domain.tools import (
    check_s3_marker,
    get_batch_jobs,
    get_tracer_run,
)

# Report formatting
from src.agent.render_output.report import ReportContext, format_problem_md, format_slack_message

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
TOOL_TIMEOUT_SECONDS = 10.0
DEFAULT_S3_BUCKET = "tracer-logs"
DEFAULT_S3_PREFIX = "events/"


# ─────────────────────────────────────────────────────────────────────────────
# Node: Plan - Deterministic rules based on alert type
# ─────────────────────────────────────────────────────────────────────────────

def node_plan(state: InvestigationState) -> dict:
    """
    Produce an explicit plan based on alert_name and affected_table.

    Uses simple rules - no LLM needed for planning.
    Only use an LLM for planning when you have many alert types and rules are breaking.
    """
    alert_name = state.get("alert_name", "").lower()
    affected_table = state.get("affected_table", "").lower()

    # Deterministic rules keyed off alert_name and affected_table
    plan_sources: list[EvidenceSource] = []

    # Rule 1: Freshness SLA alerts -> check tracer first, then storage
    if "freshness" in alert_name or "sla" in alert_name:
        plan_sources = ["tracer", "storage", "batch"]

    # Rule 2: Pipeline/job failure alerts -> check tracer and batch
    elif "pipeline" in alert_name or "job" in alert_name or "failed" in alert_name:
        plan_sources = ["tracer", "batch"]

    # Rule 3: Data missing/storage alerts -> check storage first
    elif "missing" in alert_name or "storage" in alert_name or "s3" in alert_name:
        plan_sources = ["storage", "tracer"]

    # Rule 4: Table-specific rules
    elif "events" in affected_table or "fact" in affected_table:
        plan_sources = ["tracer", "storage", "batch"]

    # Default: check everything
    else:
        plan_sources = ["tracer", "storage", "batch"]

    return {"plan_sources": plan_sources}


# ─────────────────────────────────────────────────────────────────────────────
# Node: Gather Evidence - Execute plan directly
# ─────────────────────────────────────────────────────────────────────────────

def _call_with_timeout(fn, timeout: float, **kwargs) -> tuple[Any, str | None]:
    """
    Call a function with timeout. Returns (result, error_message).
    Fails soft - returns None result with error message on failure.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, **kwargs)
        try:
            result = future.result(timeout=timeout)
            return result, None
        except FuturesTimeoutError:
            return None, f"Timeout after {timeout}s"
        except Exception as e:
            return None, str(e)


def _collect_tracer_evidence() -> dict[str, Any]:
    """Collect evidence from Tracer API."""
    result, error = _call_with_timeout(get_tracer_run, TOOL_TIMEOUT_SECONDS)

    if error:
        logger.warning(f"Tracer evidence collection failed: {error}")
        return {"found": False, "error": error}

    if not result.found:
        return {"found": False, "message": "No recent pipeline runs found"}

    return {
        "found": True,
        "run_id": result.run_id,
        "pipeline_name": result.pipeline_name,
        "run_name": result.run_name,
        "status": result.status,
        "run_time_minutes": round(result.run_time_seconds / 60, 1) if result.run_time_seconds else 0,
        "run_cost_usd": round(result.run_cost, 2) if result.run_cost else 0,
        "max_ram_gb": round(result.max_ram_gb, 1) if result.max_ram_gb else 0,
        "user_email": result.user_email,
        "team": result.team,
        "instance_type": result.instance_type,
    }


def _collect_storage_evidence() -> dict[str, Any]:
    """Collect evidence from S3."""
    result, error = _call_with_timeout(
        check_s3_marker,
        TOOL_TIMEOUT_SECONDS,
        bucket=DEFAULT_S3_BUCKET,
        prefix=DEFAULT_S3_PREFIX,
    )

    if error:
        logger.warning(f"Storage evidence collection failed: {error}")
        return {"found": False, "error": error}

    return {
        "found": True,
        "marker_exists": result.marker_exists,
        "file_count": result.file_count,
        "files": result.files,
    }


def _collect_batch_evidence() -> dict[str, Any]:
    """Collect evidence from AWS Batch via Tracer."""
    result, error = _call_with_timeout(get_batch_jobs, TOOL_TIMEOUT_SECONDS)

    if error:
        logger.warning(f"Batch evidence collection failed: {error}")
        return {"found": False, "error": error}

    if not result.found:
        return {"found": False, "message": "No AWS Batch jobs found"}

    return {
        "found": True,
        "total_jobs": result.total_jobs,
        "succeeded_jobs": result.succeeded_jobs,
        "failed_jobs": result.failed_jobs,
        "failure_reason": result.failure_reason,
        "jobs": result.jobs,
    }


# Map source names to collector functions
EVIDENCE_COLLECTORS: dict[EvidenceSource, tuple[callable, str]] = {
    "tracer": (_collect_tracer_evidence, "pipeline_run"),
    "storage": (_collect_storage_evidence, "s3"),
    "batch": (_collect_batch_evidence, "batch_jobs"),
}


def node_gather_evidence(state: InvestigationState) -> dict:
    """
    Execute the plan by calling tool functions directly.

    Iterates plan_sources and calls each tool function.
    Stores results as structured data in evidence dict.
    Fails soft - partial evidence still produces an answer.
    """
    plan_sources = state.get("plan_sources", [])
    evidence: dict[str, Any] = {}

    for source in plan_sources:
        if source not in EVIDENCE_COLLECTORS:
            logger.warning(f"Unknown evidence source: {source}")
            continue

        collector_fn, evidence_key = EVIDENCE_COLLECTORS[source]
        evidence[evidence_key] = collector_fn()

    return {"evidence": evidence}


# ─────────────────────────────────────────────────────────────────────────────
# Node: Analyze - Synthesize root cause from evidence
# ─────────────────────────────────────────────────────────────────────────────

def node_analyze(state: InvestigationState) -> dict:
    """
    Synthesize all collected evidence into a root cause conclusion.

    Uses LLM to analyze structured evidence and determine root cause.
    Pure function: no rendering, no side effects.
    """
    evidence = state.get("evidence", {})

    # Build prompt and get LLM response
    prompt = _build_analysis_prompt(state, evidence)
    response = stream_completion(prompt)

    # Parse result
    result = parse_root_cause(response)

    return {
        "root_cause": result.root_cause,
        "confidence": result.confidence,
    }


def _build_analysis_prompt(state: InvestigationState, evidence: dict) -> str:
    """Build the root cause analysis prompt from collected evidence."""
    # S3 evidence
    s3_info = "No S3 data collected"
    if evidence.get("s3"):
        s3 = evidence["s3"]
        s3_info = f"""- _SUCCESS marker exists: {s3.get('marker_exists', False)}
- Files found: {s3.get('file_count', 0)}
- Files: {s3.get('files', [])}"""

    # Pipeline run evidence
    run_info = "No pipeline run data collected"
    if evidence.get("pipeline_run") and evidence["pipeline_run"].get("found"):
        run = evidence["pipeline_run"]
        run_info = f"""- Pipeline: {run.get('pipeline_name', 'Unknown')}
- Run Name: {run.get('run_name', 'Unknown')}
- Status: {run.get('status', 'Unknown')}
- Duration: {run.get('run_time_minutes', 0)} minutes
- Cost: ${run.get('run_cost_usd', 0)}
- User: {run.get('user_email', 'Unknown')}
- Team: {run.get('team', 'Unknown')}
- Instance: {run.get('instance_type', 'Unknown')}
- Max RAM: {run.get('max_ram_gb', 0)} GB"""

    # Batch jobs evidence
    batch_info = "No AWS Batch data collected"
    if evidence.get("batch_jobs") and evidence["batch_jobs"].get("found"):
        batch = evidence["batch_jobs"]
        batch_info = f"""- Total jobs: {batch.get('total_jobs', 0)}
- Succeeded: {batch.get('succeeded_jobs', 0)}
- Failed: {batch.get('failed_jobs', 0)}
- Failure reason: {batch.get('failure_reason', 'None')}"""
        if batch.get("jobs"):
            for job in batch["jobs"][:2]:
                batch_info += f"\n- Job '{job.get('job_name')}': {job.get('status')}"
                if job.get("failure_reason"):
                    batch_info += f"\n  FAILURE: {job['failure_reason']}"

    return f"""You are an expert data infrastructure engineer. You have investigated a production incident and collected the following evidence.

## Incident
- Alert: {state['alert_name']}
- Affected Table: {state['affected_table']}

## Evidence Collected

### Pipeline Run
{run_info}

### AWS Batch Jobs
{batch_info}

### S3 Check
{s3_info}

## Task
Synthesize these findings into a root cause conclusion.

Respond in exactly this format:
ROOT_CAUSE:
* <first key finding>
* <second key finding>
* <third key finding>
* <impact on downstream systems>
CONFIDENCE: <number between 0 and 100>

Keep each bullet point concise (under 80 characters). Use exactly 3-4 bullet points."""


# ─────────────────────────────────────────────────────────────────────────────
# Node: Output - Generate formatted reports
# ─────────────────────────────────────────────────────────────────────────────

def node_output(state: InvestigationState) -> dict:
    """
    Generate Slack message and problem.md from analysis results.

    Pure function: no rendering, no side effects.
    """
    # Extract evidence for report context
    evidence = state.get("evidence", {})
    pipeline_run = evidence.get("pipeline_run", {}) if evidence else {}
    batch_jobs = evidence.get("batch_jobs", {}) if evidence else {}
    s3_data = evidence.get("s3", {}) if evidence else {}

    ctx: ReportContext = {
        "affected_table": state["affected_table"],
        "root_cause": state["root_cause"],
        "confidence": state["confidence"],
        "s3_marker_exists": s3_data.get("marker_exists", False) if s3_data else False,
        "tracer_run_status": pipeline_run.get("status") if pipeline_run else None,
        "tracer_run_name": pipeline_run.get("run_name") if pipeline_run else None,
        "tracer_pipeline_name": pipeline_run.get("pipeline_name") if pipeline_run else None,
        "tracer_run_cost": pipeline_run.get("run_cost_usd", 0) if pipeline_run else 0,
        "tracer_max_ram_gb": pipeline_run.get("max_ram_gb", 0) if pipeline_run else 0,
        "tracer_user_email": pipeline_run.get("user_email") if pipeline_run else None,
        "tracer_team": pipeline_run.get("team") if pipeline_run else None,
        "tracer_instance_type": pipeline_run.get("instance_type") if pipeline_run else None,
        "tracer_failed_tasks": 0,
        "batch_failure_reason": batch_jobs.get("failure_reason") if batch_jobs else None,
        "batch_failed_jobs": batch_jobs.get("failed_jobs", 0) if batch_jobs else 0,
    }

    return {
        "slack_message": format_slack_message(ctx),
        "problem_md": format_problem_md(ctx),
    }

