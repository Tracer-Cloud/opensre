"""
Prompt builders.

Pure functions that return prompt strings. Easy to test and version.
"""

from src.agent.infrastructure.clients import S3CheckResult, TracerRunResult, TracerTaskResult, AWSBatchJobResult


def s3_interpretation_prompt(result: S3CheckResult) -> str:
    """Build prompt for interpreting S3 check results."""
    return f"""You are investigating a data pipeline incident.

You checked S3 for output files and markers:
- _SUCCESS marker exists: {result.marker_exists}
- Files found: {result.file_count}
- File list: {result.files}

Interpret these findings in 1-2 bullet points. What does this tell us about the pipeline state?
Be concise (under 80 chars per bullet). Start each line with *"""


def tracer_run_interpretation_prompt(result: TracerRunResult) -> str:
    """Build prompt for interpreting Tracer pipeline run results."""
    if not result.found:
        return """You are investigating a data pipeline incident.

No pipeline runs were found in Tracer. This could mean:
- The pipeline has not run recently
- Tracer is not properly configured
- The pipeline name filter is incorrect

Interpret this in 1-2 bullet points. Start each line with *"""
    
    # Status interpretation
    is_failed = result.status and result.status.lower() == "failed"
    status_note = "CRITICAL: Pipeline status is FAILED" if is_failed else f"Pipeline status: {result.status}"
    
    return f"""You are investigating a data pipeline incident.

You queried Tracer for pipeline run information:
- Pipeline: {result.pipeline_name}
- Run Name: {result.run_name}
- Status: {result.status} {'[FAILED - INVESTIGATE]' if is_failed else ''}
- Duration: {result.run_time_seconds:.0f} seconds ({result.run_time_seconds/60:.1f} min)
- Cost: ${result.run_cost:.2f}
- User: {result.user_email}
- Team: {result.team}
- Instance: {result.instance_type}
- Max RAM: {result.max_ram_gb:.1f} GB

{status_note}

Interpret these findings in 1-2 bullet points. If status is Failed, emphasize that the pipeline FAILED.
Be concise (under 80 chars per bullet). Start each line with *"""


def tracer_tasks_interpretation_prompt(result: TracerTaskResult) -> str:
    """Build prompt for interpreting Tracer task results."""
    if not result.found:
        return """You are investigating a data pipeline incident.

No tasks were found for this pipeline run. This is unusual.

Interpret this in 1-2 bullet points. Start each line with *"""
    
    failed_summary = ""
    if result.failed_task_details:
        failed_summary = "\n\nFailed tasks:\n"
        for task in result.failed_task_details[:3]:  # Limit to top 3
            failed_summary += f"- {task['tool_name']}: exit_code={task.get('exit_code', 'unknown')}"
            if task.get('reason'):
                failed_summary += f", reason={task['reason']}"
            if task.get('explanation'):
                failed_summary += f"\n  {task['explanation'][:100]}"
            failed_summary += "\n"
    
    return f"""You are investigating a data pipeline incident.

Task summary from Tracer:
- Total tasks: {result.total_tasks}
- Completed: {result.completed_tasks}
- Failed: {result.failed_tasks}
{failed_summary}
Interpret these findings in 1-2 bullet points. What do the task results tell us about the failure?
Be concise (under 80 chars per bullet). Start each line with *"""


def batch_jobs_interpretation_prompt(result: AWSBatchJobResult) -> str:
    """Build prompt for interpreting AWS Batch job results."""
    if not result.found:
        return """You are investigating a data pipeline incident.

No AWS Batch jobs were found for this pipeline run. This could mean:
- The pipeline doesn't use AWS Batch
- Jobs have not been submitted yet
- Jobs data is not yet available

Interpret this in 1-2 bullet points. Start each line with *"""
    
    job_summary = ""
    if result.jobs:
        job_summary = "\n\nBatch job details:\n"
        for job in result.jobs[:3]:  # Limit to top 3
            job_summary += f"- {job['job_name']}: status={job['status']}"
            if job.get('failure_reason'):
                job_summary += f"\n  FAILURE: {job['failure_reason']}"
            if job.get('status_reason'):
                job_summary += f"\n  Reason: {job['status_reason']}"
            job_summary += f"\n  Resources: {job.get('vcpu', 0)} vCPU, {job.get('memory_mb', 0)/1024:.0f} GB RAM, {job.get('gpu_count', 0)} GPU\n"
    
    return f"""You are investigating a data pipeline incident.

AWS Batch job summary:
- Total jobs: {result.total_jobs}
- Succeeded: {result.succeeded_jobs}
- Failed: {result.failed_jobs}
- Main failure reason: {result.failure_reason or 'None'}
{job_summary}
Interpret these findings in 1-2 bullet points. Focus on the failure reason if present.
Be concise (under 80 chars per bullet). Start each line with *"""


def root_cause_synthesis_prompt(
    alert_name: str,
    affected_table: str,
    s3_marker_exists: bool,
    s3_file_count: int,
    tracer_run: TracerRunResult | None,
    tracer_tasks: TracerTaskResult | None,
    batch_jobs: AWSBatchJobResult | None = None,
) -> str:
    """Build prompt for synthesizing root cause from all evidence."""
    run_info = "No run data available"
    is_failed = False
    if tracer_run and tracer_run.found:
        is_failed = tracer_run.status and tracer_run.status.lower() == "failed"
        status_marker = "[FAILED]" if is_failed else ""
        run_info = f"""- Pipeline: {tracer_run.pipeline_name}
- Run: {tracer_run.run_name}
- Status: {tracer_run.status} {status_marker}
- Duration: {tracer_run.run_time_seconds:.0f}s ({tracer_run.run_time_seconds/60:.1f} min)
- Cost: ${tracer_run.run_cost:.2f}
- User: {tracer_run.user_email}
- Team: {tracer_run.team}
- Instance: {tracer_run.instance_type}
- Max RAM: {tracer_run.max_ram_gb:.1f} GB"""
    
    task_info = "No task data available"
    if tracer_tasks and tracer_tasks.found:
        task_info = f"""- Total tasks: {tracer_tasks.total_tasks}
- Completed: {tracer_tasks.completed_tasks}
- Failed: {tracer_tasks.failed_tasks}"""
        if tracer_tasks.failed_task_details:
            task_info += "\n- Failed task details:"
            for task in tracer_tasks.failed_task_details[:3]:
                task_info += f"\n  * {task['tool_name']}: {task.get('reason', 'unknown error')}"
    
    batch_info = "No AWS Batch data available"
    if batch_jobs and batch_jobs.found:
        batch_info = f"""- Total batch jobs: {batch_jobs.total_jobs}
- Succeeded: {batch_jobs.succeeded_jobs}
- Failed: {batch_jobs.failed_jobs}"""
        if batch_jobs.failure_reason:
            batch_info += f"\n- **FAILURE REASON**: {batch_jobs.failure_reason}"
        if batch_jobs.jobs:
            for job in batch_jobs.jobs[:2]:
                batch_info += f"\n- Job '{job['job_name']}': {job.get('status', 'unknown')}"
                if job.get('failure_reason'):
                    batch_info += f"\n  **FAILURE**: {job['failure_reason']}"
                    batch_info += f"\n  Exit code: {job.get('exit_code', 'unknown')}"
                batch_info += f"\n  Resources: {job.get('vcpu', 0)} vCPU, {job.get('memory_mb', 0)/1024:.0f} GB RAM, {job.get('gpu_count', 0)} GPU"
    
    # Emphasize if the run status is Failed
    status_warning = ""
    if is_failed:
        status_warning = "\n\n**IMPORTANT**: The pipeline run status is FAILED. This is the primary indicator of failure."
    
    return f"""You are an expert data infrastructure engineer. You have investigated a production incident and collected the following evidence.

## Incident
- Alert: {alert_name}
- Affected Table: {affected_table}

## Evidence Collected

### Tracer Pipeline Run
{run_info}
{status_warning}

### AWS Batch Job Results
{batch_info}

### Tracer Tool Results
{task_info}

### S3 Check Results
- _SUCCESS marker exists: {s3_marker_exists}
- Files in output prefix: {s3_file_count}

## Task
Synthesize these findings into a root cause conclusion. 
- Pay special attention to the pipeline STATUS (Failed means the job failed!)
- The AWS Batch failure reason tells you WHY it failed (e.g., OutOfMemoryError)
- Include the specific error reason in your analysis

Respond in exactly this format:
ROOT_CAUSE:
* <first key finding - pipeline status>
* <second key finding - the specific failure reason>
* <third key finding - resource/infrastructure cause>
* <impact on downstream systems>
CONFIDENCE: <number between 0 and 100>

Keep each bullet point concise (under 80 characters). Use exactly 3-4 bullet points."""

