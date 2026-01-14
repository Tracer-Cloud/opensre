"""
Report formatters for Slack and problem.md.

Pure functions that format state into output strings.
"""

from typing import TypedDict


class ReportContext(TypedDict, total=False):
    affected_table: str
    root_cause: str
    confidence: float
    s3_marker_exists: bool
    tracer_run_status: str | None
    tracer_run_name: str | None
    tracer_pipeline_name: str | None
    tracer_run_cost: float
    tracer_max_ram_gb: float
    tracer_user_email: str | None
    tracer_team: str | None
    tracer_instance_type: str | None
    tracer_failed_tasks: int
    batch_failure_reason: str | None
    batch_failed_jobs: int


def format_slack_message(ctx: ReportContext) -> str:
    """Format the Slack message output."""
    status = ctx.get('tracer_run_status', 'unknown')
    is_failed = status.lower() == 'failed' if status else False
    status_marker = "[FAILED]" if is_failed else ""
    
    batch_info = ""
    if ctx.get("batch_failure_reason"):
        batch_info = f"* Failure Reason: {ctx['batch_failure_reason']}\n"
    
    return f"""[RCA] {ctx['affected_table']} freshness incident
Analyzed by: pipeline-agent
Detected: 02:13 UTC

*Conclusion*
{ctx['root_cause']}

*Evidence from Tracer*
* Pipeline: {ctx.get('tracer_pipeline_name', 'unknown')}
* Run: {ctx.get('tracer_run_name', 'unknown')}
* Status: {status} {status_marker}
* User: {ctx.get('tracer_user_email', 'unknown')}
* Team: {ctx.get('tracer_team', 'unknown')}
* Cost: ${ctx.get('tracer_run_cost', 0):.2f}
* Instance: {ctx.get('tracer_instance_type', 'unknown')}
* Max RAM: {ctx.get('tracer_max_ram_gb', 0):.1f} GB
{batch_info}* S3 _SUCCESS marker: {'not found' if not ctx.get('s3_marker_exists') else 'present'}

*Confidence:* {ctx['confidence']:.2f}

*Recommended Actions*
1. Review failed job in Tracer dashboard
2. {'Increase memory allocation - job killed due to ' + ctx.get('batch_failure_reason', 'OOM') if ctx.get('batch_failure_reason') and 'memory' in ctx.get('batch_failure_reason', '').lower() else 'Check AWS Batch logs for error details'}
3. Rerun pipeline after fixing issues
"""


def format_problem_md(ctx: ReportContext) -> str:
    """Format the problem.md report."""
    status = ctx.get('tracer_run_status', 'unknown')
    is_failed = status.lower() == 'failed' if status else False
    
    batch_section = ""
    if ctx.get("batch_failure_reason"):
        batch_section = f"""
### AWS Batch Job Failure
- Failed jobs: {ctx.get('batch_failed_jobs', 0)}
- **Failure reason**: `{ctx.get('batch_failure_reason')}`
"""
    
    return f"""# Incident Report: {ctx['affected_table']} Freshness SLA Breach

## Summary
{ctx['root_cause']}

## Evidence from Tracer

### Pipeline Run Details
| Field | Value |
|-------|-------|
| Pipeline | `{ctx.get('tracer_pipeline_name', 'unknown')}` |
| Run Name | `{ctx.get('tracer_run_name', 'unknown')}` |
| Status | **{status}** {'[FAILED]' if is_failed else ''} |
| User | {ctx.get('tracer_user_email', 'unknown')} |
| Team | {ctx.get('tracer_team', 'unknown')} |
| Cost | ${ctx.get('tracer_run_cost', 0):.2f} |
| Instance | {ctx.get('tracer_instance_type', 'unknown')} |
| Max RAM | {ctx.get('tracer_max_ram_gb', 0):.1f} GB |
{batch_section}
### S3 State
- Bucket: `tracer-logs`
- `_SUCCESS` marker: {'present' if ctx.get('s3_marker_exists') else '**missing**'}

## Root Cause Analysis
Confidence: {ctx['confidence']:.0%}

{ctx['root_cause']}

## Recommended Actions
1. Review failed job in Tracer dashboard at https://staging.tracer.cloud
2. {'**Increase memory allocation** - job was killed due to OutOfMemoryError' if ctx.get('batch_failure_reason') and 'memory' in ctx.get('batch_failure_reason', '').lower() else 'Check AWS Batch logs for error details'}
3. Consider using a larger instance type with more RAM
4. Rerun pipeline after fixing resource allocation
"""

