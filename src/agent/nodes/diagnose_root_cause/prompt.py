"""Prompt building for root cause diagnosis."""

from src.agent.state import InvestigationState


def build_diagnosis_prompt(state: InvestigationState, evidence: dict) -> str:
    """Build analysis prompt from evidence."""
    # Format each evidence section
    s3 = evidence.get("s3", {})
    s3_info = f"- Marker: {s3.get('marker_exists')}, Files: {s3.get('file_count', 0)}" if s3.get("found") else "No S3 data"

    run = evidence.get("pipeline_run", {})
    run_info = "No pipeline data"
    if run.get("found"):
        run_info = f"""- Pipeline: {run.get('pipeline_name')} | Status: {run.get('status')}
- Duration: {run.get('run_time_minutes', 0)}min | Cost: ${run.get('run_cost_usd', 0)}
- User: {run.get('user_email')} | Team: {run.get('team')}"""

    batch = evidence.get("batch_jobs", {})
    batch_info = "No batch data"
    if batch.get("found"):
        batch_info = f"- Jobs: {batch.get('total_jobs')} total, {batch.get('failed_jobs')} failed"
        if batch.get("failure_reason"):
            batch_info += f"\n- Failure: {batch['failure_reason']}"

    web_run = evidence.get("tracer_web_run", {})
    web_run_info = "No web app run data"
    if web_run.get("found"):
        web_run_info = (
            f"- Pipeline: {web_run.get('pipeline_name')} | Status: {web_run.get('status')}\n"
            f"- Run: {web_run.get('run_name')} | Trace: {web_run.get('trace_id')}\n"
            f"- Cost: ${web_run.get('run_cost', 0)} | User: {web_run.get('user_email')}\n"
            f"- Instance: {web_run.get('instance_type')}"
        )

        # Add failed jobs details
        failed_jobs = web_run.get("failed_jobs", [])
        if failed_jobs:
            web_run_info += f"\n- Failed jobs: {len(failed_jobs)}"
            for job in failed_jobs[:3]:
                job_name = job.get("job_name", "Unknown")
                status_reason = job.get("status_reason", "")
                exit_code = job.get("exit_code")
                container_reason = job.get("container_reason")
                web_run_info += f"\n  * {job_name}: {status_reason}"
                if exit_code:
                    web_run_info += f" (exit_code={exit_code})"
                if container_reason:
                    web_run_info += f" - {container_reason}"

        # Add failed tools details
        failed_tools = web_run.get("failed_tools", [])
        if failed_tools:
            web_run_info += f"\n- Failed tools: {len(failed_tools)}"
            for tool in failed_tools[:3]:
                tool_name = tool.get("tool_name", "Unknown")
                exit_code = tool.get("exit_code")
                reason = tool.get("reason")
                web_run_info += f"\n  * {tool_name}: exit_code={exit_code}"
                if reason:
                    web_run_info += f" - {reason}"

        # Add error logs summary
        error_logs = web_run.get("error_logs", [])
        if error_logs:
            web_run_info += f"\n- Error logs: {len(error_logs)} found"
            for log in error_logs[:2]:
                msg = str(log.get("message", ""))[:150]
                web_run_info += f"\n  * {msg}"

    return f"""Analyze this incident and determine root cause.

## Incident
Alert: {state['alert_name']} | Table: {state['affected_table']}

## Evidence
### Pipeline: {run_info}
### Web App Runs: {web_run_info}
### Batch: {batch_info}
### S3: {s3_info}

Focus on:
- Exit codes from failed jobs/tools (non-zero exit codes indicate specific failure modes)
- Container failure reasons (memory, timeout, signal, etc.)
- Job status reasons (Essential container exited, Task timed out, etc.)
- Error patterns in logs
- Correlation between failed jobs and failed tools

Respond in this format:
ROOT_CAUSE:
* <finding 1>
* <finding 2>
* <finding 3>
CONFIDENCE: <0-100>"""
