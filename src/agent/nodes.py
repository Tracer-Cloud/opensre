"""
LangGraph nodes.

Nodes orchestrate tools, LLM, and rendering. They only return state patches.
"""

# Domain layer
from src.agent.domain.state import InvestigationState
from src.agent.domain.tools import check_s3_marker, get_tracer_run, get_tracer_tasks, get_batch_jobs
from src.agent.domain.prompts import (
    s3_interpretation_prompt,
    tracer_run_interpretation_prompt,
    tracer_tasks_interpretation_prompt,
    batch_jobs_interpretation_prompt,
    root_cause_synthesis_prompt,
)
from src.agent.infrastructure.clients import TracerRunResult, TracerTaskResult, AWSBatchJobResult

# Infrastructure layer
from src.agent.infrastructure.llm import stream_completion, parse_bullets, parse_root_cause

# Presentation layer
from src.agent.presentation.render import (
    render_step_header,
    render_api_response,
    render_tracer_run_details,
    render_batch_job_details,
    render_llm_thinking,
    render_dot,
    render_newline,
    render_bullets,
    render_root_cause_complete,
    render_generating_outputs,
    render_hypothesis_header,
    render_hypotheses,
    render_hypothesis_testing,
    render_hypothesis_result,
)
from src.agent.presentation.report import format_slack_message, format_problem_md, ReportContext

# Hypothesis model
from src.models.hypothesis import HYPOTHESIS_TEMPLATES


# ─────────────────────────────────────────────────────────────────────────────
# Node: Propose Hypotheses
# ─────────────────────────────────────────────────────────────────────────────

def node_propose_hypotheses(state: InvestigationState) -> dict:
    """Propose hypotheses to investigate based on the alert."""
    render_hypothesis_header()
    
    # For this demo, we use predefined hypothesis templates
    # In production, this could use LLM to generate hypotheses dynamically
    hypotheses = [
        {
            "id": h["id"],
            "name": h["name"],
            "description": h["description"],
            "tools_to_use": h["tools_to_use"],
            "status": "pending",
            "confidence": 0.0,
        }
        for h in HYPOTHESIS_TEMPLATES
    ]
    
    render_hypotheses(hypotheses)
    
    return {
        "hypotheses": hypotheses,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: Execute Hypotheses (Check S3, Tracer, Batch Jobs)
# ─────────────────────────────────────────────────────────────────────────────

def node_check_s3(state: InvestigationState) -> dict:
    """Check S3 and interpret results with LLM (tests H2: Output files missing)."""
    render_hypothesis_testing("H2: Output files missing")
    render_step_header(1, "Checking S3 for data artifacts...")
    
    # Tool call
    result = check_s3_marker("tracer-logs", "events/2026-01-13/")
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
# Node: Check Tracer Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def node_check_tracer(state: InvestigationState) -> dict:
    """Check Tracer for pipeline run, tasks, and AWS Batch jobs (tests H1 and H3)."""
    render_hypothesis_testing("H1: Pipeline task failed / H3: Resource exhaustion")
    render_step_header(2, "Fetching pipeline run from Tracer...")
    
    # Get pipeline run from batch-runs endpoint
    run_result = get_tracer_run()
    is_failed = run_result.status and run_result.status.lower() == "failed"
    
    if run_result.found:
        # Show detailed run info in table format
        render_tracer_run_details(
            pipeline_name=run_result.pipeline_name or "Unknown",
            run_name=run_result.run_name or "Unknown",
            status=run_result.status or "Unknown",
            user_email=run_result.user_email or "Unknown",
            team=run_result.team or "Unknown",
            run_cost=run_result.run_cost,
            runtime_seconds=run_result.run_time_seconds,
            instance_type=run_result.instance_type or "Unknown",
            max_ram_gb=run_result.max_ram_gb,
        )
    else:
        render_api_response("Tracer Run", "No runs found", is_error=True)
    
    # LLM interpretation for run
    render_llm_thinking()
    prompt = tracer_run_interpretation_prompt(run_result)
    response = stream_completion(prompt, on_chunk=lambda _: render_dot())
    render_newline()
    
    interpretation = parse_bullets(response)
    render_bullets(interpretation.bullets, is_error=is_failed)
    
    # Get AWS Batch jobs for failure details (this has the real task data)
    tasks_result = TracerTaskResult(found=False, total_tasks=0, failed_tasks=0, completed_tasks=0, tasks=[], failed_task_details=[])
    batch_result = AWSBatchJobResult(found=False, total_jobs=0, failed_jobs=0, succeeded_jobs=0, jobs=[], failure_reason=None)
    
    if run_result.found:
        render_step_header(3, "Fetching AWS Batch jobs (tasks)...")
        batch_result = get_batch_jobs()
        
        if batch_result.found:
            has_failures = batch_result.failed_jobs > 0
            render_api_response(
                "AWS Batch", 
                f"total={batch_result.total_jobs}, failed={batch_result.failed_jobs}, succeeded={batch_result.succeeded_jobs}",
                is_error=has_failures
            )
            
            # Show detailed job info for each job
            for job in batch_result.jobs:
                render_batch_job_details(
                    job_name=job.get("job_name", "Unknown"),
                    status=job.get("status", "Unknown"),
                    failure_reason=job.get("failure_reason"),
                    exit_code=job.get("exit_code"),
                    vcpu=job.get("vcpu", 0),
                    memory_gb=job.get("memory_mb", 0) / 1024,
                    gpu_count=job.get("gpu_count", 0),
                )
        else:
            render_api_response("AWS Batch", "No batch jobs found", is_error=False)
        
        # LLM interpretation for batch jobs
        render_llm_thinking()
        prompt = batch_jobs_interpretation_prompt(batch_result)
        response = stream_completion(prompt, on_chunk=lambda _: render_dot())
        render_newline()
        
        interpretation = parse_bullets(response)
        render_bullets(interpretation.bullets, is_error=batch_result.failure_reason is not None)
        
        # Also get tool info from Tracer for additional context
        render_step_header(4, "Fetching tool details from Tracer...")
        tasks_result = get_tracer_tasks(run_result.run_id)
        render_api_response(
            "Tracer Tools", 
            f"total={tasks_result.total_tasks}, failed={tasks_result.failed_tasks}, completed={tasks_result.completed_tasks}",
            is_error=tasks_result.failed_tasks > 0
        )
        
        # LLM interpretation for tasks
        render_llm_thinking()
        prompt = tracer_tasks_interpretation_prompt(tasks_result)
        response = stream_completion(prompt, on_chunk=lambda _: render_dot())
        render_newline()
        
        interpretation = parse_bullets(response)
        render_bullets(interpretation.bullets, is_error=tasks_result.failed_tasks > 0)
    
    return {
        "tracer_run_found": run_result.found,
        "tracer_run_id": run_result.run_id,
        "tracer_pipeline_name": run_result.pipeline_name,
        "tracer_run_name": run_result.run_name,
        "tracer_run_status": run_result.status,
        "tracer_run_time_seconds": run_result.run_time_seconds,
        "tracer_run_cost": run_result.run_cost,
        "tracer_max_ram_gb": run_result.max_ram_gb,
        "tracer_user_email": run_result.user_email,
        "tracer_team": run_result.team,
        "tracer_instance_type": run_result.instance_type,
        "tracer_total_tasks": tasks_result.total_tasks,
        "tracer_failed_tasks": tasks_result.failed_tasks,
        "tracer_failed_task_details": tasks_result.failed_task_details,
        "batch_jobs_found": batch_result.found,
        "batch_total_jobs": batch_result.total_jobs,
        "batch_failed_jobs": batch_result.failed_jobs,
        "batch_failure_reason": batch_result.failure_reason,
        "batch_job_details": batch_result.jobs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node: Determine Root Cause
# ─────────────────────────────────────────────────────────────────────────────

def node_determine_root_cause(state: InvestigationState) -> dict:
    """Synthesize all evidence into root cause conclusion."""
    render_step_header(5, "Synthesizing root cause analysis...")
    
    # Build Tracer result objects for prompt
    tracer_run = TracerRunResult(
        found=state.get("tracer_run_found", False),
        run_id=state.get("tracer_run_id"),
        pipeline_name=state.get("tracer_pipeline_name"),
        run_name=state.get("tracer_run_name"),
        status=state.get("tracer_run_status"),
        start_time=None,
        end_time=None,
        run_time_seconds=state.get("tracer_run_time_seconds", 0),
        run_cost=state.get("tracer_run_cost", 0),
        max_ram_gb=state.get("tracer_max_ram_gb", 0),
        user_email=state.get("tracer_user_email"),
        team=state.get("tracer_team"),
        department=None,
        instance_type=state.get("tracer_instance_type"),
        environment=None,
        region=None,
        tool_count=state.get("tracer_total_tasks", 0),
    )
    
    tracer_tasks = TracerTaskResult(
        found=state.get("tracer_total_tasks", 0) > 0,
        total_tasks=state.get("tracer_total_tasks", 0),
        failed_tasks=state.get("tracer_failed_tasks", 0),
        completed_tasks=state.get("tracer_total_tasks", 0) - state.get("tracer_failed_tasks", 0),
        tasks=[],
        failed_task_details=state.get("tracer_failed_task_details", []),
    )
    
    batch_jobs = AWSBatchJobResult(
        found=state.get("batch_jobs_found", False),
        total_jobs=state.get("batch_total_jobs", 0),
        failed_jobs=state.get("batch_failed_jobs", 0),
        succeeded_jobs=state.get("batch_total_jobs", 0) - state.get("batch_failed_jobs", 0),
        jobs=state.get("batch_job_details", []),
        failure_reason=state.get("batch_failure_reason"),
    )
    
    # LLM synthesis
    render_llm_thinking()
    prompt = root_cause_synthesis_prompt(
        alert_name=state["alert_name"],
        affected_table=state["affected_table"],
        s3_marker_exists=state.get("s3_marker_exists", False),
        s3_file_count=state.get("s3_file_count", 0),
        tracer_run=tracer_run,
        tracer_tasks=tracer_tasks,
        batch_jobs=batch_jobs,
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
        "s3_marker_exists": state.get("s3_marker_exists", False),
        "tracer_run_status": state.get("tracer_run_status"),
        "tracer_run_name": state.get("tracer_run_name"),
        "tracer_pipeline_name": state.get("tracer_pipeline_name"),
        "tracer_run_cost": state.get("tracer_run_cost", 0),
        "tracer_max_ram_gb": state.get("tracer_max_ram_gb", 0),
        "tracer_user_email": state.get("tracer_user_email"),
        "tracer_team": state.get("tracer_team"),
        "tracer_instance_type": state.get("tracer_instance_type"),
        "tracer_failed_tasks": state.get("tracer_failed_tasks", 0),
        "batch_failure_reason": state.get("batch_failure_reason"),
        "batch_failed_jobs": state.get("batch_failed_jobs", 0),
    }
    
    return {
        "slack_message": format_slack_message(ctx),
        "problem_md": format_problem_md(ctx),
    }

