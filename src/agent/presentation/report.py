"""
Report formatters for Slack and problem.md.

Pure functions that format state into output strings.
"""

from typing import TypedDict


class ReportContext(TypedDict):
    affected_table: str
    root_cause: str
    confidence: float
    s3_marker_exists: bool
    nextflow_finalize_status: str | None


def format_slack_message(ctx: ReportContext) -> str:
    """Format the Slack message output."""
    return f"""🧠 *RCA — {ctx['affected_table']} freshness incident*
Analyzed by: pipeline-agent
Detected: 02:13 UTC

*Conclusion*
{ctx['root_cause']}

*Evidence chain*
• Raw input file present in S3
• `events_processed.parquet` written successfully
• Nextflow finalize step: {ctx['nextflow_finalize_status']} after 5 retries
• `_SUCCESS` marker: {'not found' if not ctx['s3_marker_exists'] else 'present'}
• Service B loader running, blocked on `_SUCCESS`

*Confidence:* {ctx['confidence']:.2f}

*Actions*
1. Grant Nextflow role `s3:PutObject` on the `_SUCCESS` path
2. Rerun Nextflow finalize step
"""


def format_problem_md(ctx: ReportContext) -> str:
    """Format the problem.md report."""
    return f"""# Incident Report: {ctx['affected_table']} Freshness SLA Breach

## Summary
{ctx['root_cause']}

## Evidence

### S3 State
- Bucket: `tracer-processed-data`
- Prefix: `events/2026-01-13/`
- `_SUCCESS` marker: {'present' if ctx['s3_marker_exists'] else '**missing**'}

### Nextflow Pipeline
- Pipeline: `events-etl`
- Finalize status: `{ctx['nextflow_finalize_status']}`

## Root Cause Analysis
Confidence: {ctx['confidence']:.0%}

{ctx['root_cause']}

## Recommended Actions
1. Grant Nextflow IAM role `s3:PutObject` permission on the `_SUCCESS` path
2. Rerun the Nextflow finalize step
3. Monitor Service B loader for successful pickup
"""

